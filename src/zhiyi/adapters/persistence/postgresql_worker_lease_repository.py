"""Atomic PostgreSQL Worker lease claim coordination."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from typing import cast as type_cast
from uuid import UUID

from sqlalchemy import and_, case, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import DBAPIError, IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncTransaction

from zhiyi.adapters.persistence.postgresql_schema import runs
from zhiyi.adapters.persistence.postgresql_transaction_support import (
    PostgreSQLTransactionSettings,
    StorageFailureDisposition,
    TransactionPhase,
    apply_transaction_settings,
    classify_storage_failure,
    execute_once,
    rollback_if_active,
    worker_lease_storage_error,
)
from zhiyi.adapters.persistence.postgresql_worker_lease_codecs import (
    StoredClaimOutcome,
    WorkerLeaseClaimReceiptRecord,
    WorkerLeaseRecord,
    claim_id_issued_at,
    decode_claim_receipt,
    decode_inactive_running,
    decode_worker_lease,
    encode_claim_receipt,
    encode_worker_lease,
)
from zhiyi.adapters.persistence.postgresql_worker_lease_schema import (
    worker_lease_claim_receipts,
    worker_leases,
)
from zhiyi.application.commands.worker_leases import (
    ClaimLeaseCommand,
    ReleaseLeaseCommand,
    RenewLeaseCommand,
)
from zhiyi.application.ports.lease_token_generator import LeaseTokenGenerator
from zhiyi.application.ports.worker_lease_observability import (
    LeaseOperation,
    LeaseOperationObservation,
    LeaseTransactionPhase,
    WorkerLeaseTelemetry,
    deliver_terminal_observation,
)
from zhiyi.application.ports.worker_lease_repository import WorkerLeaseRepository
from zhiyi.domain.runs.identifiers import RunId, TenantId
from zhiyi.domain.worker_leases.errors import WorkerLeaseError, WorkerLeaseErrorCode
from zhiyi.domain.worker_leases.identifiers import LeaseAttemptNo, LeaseClaimId, LeaseVersion
from zhiyi.domain.worker_leases.models import (
    ConditionalLeaseOutcome,
    InactiveRunningCursor,
    InactiveRunningLease,
    InactiveRunningPage,
    LeaseAuthority,
    LeaseAuthorityProof,
    LeaseClaimOutcome,
    LeaseGrant,
    renew_by_at,
)
from zhiyi.infrastructure.database.schema_compatibility import (
    ensure_worker_lease_schema_compatible,
)
from zhiyi.infrastructure.security.lease_tokens import (
    digest_lease_token,
    lease_token_matches,
)


class _ClaimRaceLost(Exception):
    pass


@dataclass(frozen=True, slots=True)
class _ClaimExecution:
    outcome: LeaseClaimOutcome
    contended: bool


def _record(row: Any) -> Mapping[str, object]:
    return type_cast(Mapping[str, object], row._mapping)


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    value = getattr(diagnostic, "constraint_name", None)
    return value if type(value) is str else None


def _lease_select() -> Any:
    return select(
        worker_leases.c.tenant_id,
        worker_leases.c.run_id,
        worker_leases.c.worker_id,
        worker_leases.c.claim_id,
        worker_leases.c.token_digest,
        worker_leases.c.attempt_no,
        worker_leases.c.lease_version,
        worker_leases.c.duration_seconds,
        worker_leases.c.acquired_at,
        worker_leases.c.heartbeat_at,
        worker_leases.c.lease_expires_at,
        worker_leases.c.released_at,
        worker_leases.c.record_format_version,
    )


def _receipt_select() -> Any:
    return select(
        worker_lease_claim_receipts.c.tenant_id,
        worker_lease_claim_receipts.c.claim_id,
        worker_lease_claim_receipts.c.claim_issued_at,
        worker_lease_claim_receipts.c.replay_expires_at,
        worker_lease_claim_receipts.c.worker_id,
        worker_lease_claim_receipts.c.duration_seconds,
        worker_lease_claim_receipts.c.intent_format_version,
        worker_lease_claim_receipts.c.intent_fingerprint,
        worker_lease_claim_receipts.c.outcome,
        worker_lease_claim_receipts.c.run_id,
        worker_lease_claim_receipts.c.attempt_no,
        worker_lease_claim_receipts.c.initial_lease_version,
        worker_lease_claim_receipts.c.lease_acquired_at,
        worker_lease_claim_receipts.c.lease_expires_at,
        worker_lease_claim_receipts.c.replay_token,
        worker_lease_claim_receipts.c.created_at,
        worker_lease_claim_receipts.c.record_format_version,
    )


class PostgreSQLWorkerLeaseRepository(WorkerLeaseRepository):
    """Persist claim receipts and current/latest lease ownership in one transaction."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        telemetry: WorkerLeaseTelemetry,
        token_generator: LeaseTokenGenerator,
        lock_timeout_ms: int = 5_000,
        statement_timeout_ms: int = 5_000,
    ) -> None:
        if not isinstance(telemetry, WorkerLeaseTelemetry):
            raise TypeError("telemetry must implement WorkerLeaseTelemetry")
        if not isinstance(token_generator, LeaseTokenGenerator):
            raise TypeError("token_generator must implement LeaseTokenGenerator")
        self._engine = engine
        self._telemetry = telemetry
        self._token_generator = token_generator
        self._settings = PostgreSQLTransactionSettings(
            lock_timeout_ms=lock_timeout_ms,
            statement_timeout_ms=statement_timeout_ms,
        )

    async def issue_claim_id(self) -> LeaseClaimId:
        try:
            await ensure_worker_lease_schema_compatible(self._engine)
            async with self._engine.connect() as connection:
                value = await connection.scalar(text("SELECT uuidv7()"))
            if not isinstance(value, UUID):
                raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)
            claim_id = LeaseClaimId(value)
        except WorkerLeaseError as error:
            self._observe_issue(error.code.value)
            raise
        except (DBAPIError, SQLAlchemyError, ConnectionError) as error:
            public = worker_lease_storage_error(StorageFailureDisposition.UNAVAILABLE)
            self._observe_issue(public.code.value)
            raise public from error
        self._observe_issue("issued")
        return claim_id

    def _observe_issue(self, outcome_code: str) -> None:
        deliver_terminal_observation(
            self._telemetry,
            LeaseOperationObservation(
                operation=LeaseOperation.ISSUE_CLAIM_ID,
                terminal_phase=LeaseTransactionPhase.COMPLETE,
                outcome_code=outcome_code,
                correlation_id=None,
                tenant_id=None,
                run_id=None,
                worker_id=None,
                claim_id=None,
                duration_bucket=None,
                replayed=False,
                empty=False,
                contended=False,
            ),
        )

    async def claim(self, command: ClaimLeaseCommand) -> LeaseClaimOutcome:
        try:
            command = self._require_claim_command(command)
        except WorkerLeaseError as error:
            self._observe_claim(None, error)
            raise error
        try:
            await ensure_worker_lease_schema_compatible(self._engine)
            try:
                execution = await self._claim_transaction(command)
            except _ClaimRaceLost:
                execution = await self._replay_after_race(command)
        except WorkerLeaseError as error:
            self._observe_claim(command, error)
            raise
        self._observe_claim(command, execution)
        return execution.outcome

    @staticmethod
    def _require_claim_command(value: object) -> ClaimLeaseCommand:
        if not isinstance(value, ClaimLeaseCommand):
            raise WorkerLeaseError(WorkerLeaseErrorCode.INVALID_INPUT)
        return value

    def _observe_claim(
        self,
        command: ClaimLeaseCommand | None,
        result: _ClaimExecution | WorkerLeaseError,
    ) -> None:
        if isinstance(result, WorkerLeaseError):
            outcome_code = result.code.value
            replayed = False
            empty = False
            contended = False
            phase = (
                LeaseTransactionPhase.COMMIT
                if result.code is WorkerLeaseErrorCode.COMMIT_OUTCOME_UNKNOWN
                else LeaseTransactionPhase.COMPLETE
            )
            run_id = None
        else:
            outcome_code = result.outcome.code.value
            replayed = result.outcome.replayed
            empty = result.outcome.grant is None
            contended = result.contended
            phase = LeaseTransactionPhase.COMPLETE
            run_id = result.outcome.grant.run_id if result.outcome.grant is not None else None
        deliver_terminal_observation(
            self._telemetry,
            LeaseOperationObservation(
                operation=LeaseOperation.CLAIM,
                terminal_phase=phase,
                outcome_code=outcome_code,
                correlation_id=None,
                tenant_id=command.tenant_id if command is not None else None,
                run_id=run_id,
                worker_id=command.worker_id if command is not None else None,
                claim_id=command.claim_id if command is not None else None,
                duration_bucket="10-30s" if command is not None else None,
                replayed=replayed,
                empty=empty,
                contended=contended,
            ),
        )

    async def _claim_transaction(self, command: ClaimLeaseCommand) -> _ClaimExecution:
        connection: AsyncConnection | None = None
        transaction: AsyncTransaction | None = None
        phase = TransactionPhase.ACQUIRE
        try:
            connection = await self._engine.connect()
            phase = TransactionPhase.BEGIN
            transaction = await connection.begin()
            await apply_transaction_settings(connection, self._settings)
            request_now = await self._database_now(connection)
            self._validate_claim_age(command.claim_id, request_now)

            phase = TransactionPhase.ARBITRATION
            receipt = await self._load_receipt(connection, command)
            if receipt is not None:
                outcome = await self._outcome_from_receipt(
                    connection,
                    command,
                    receipt,
                    platform_now=request_now,
                    replayed=True,
                )
                await execute_once(lambda: self._commit_transaction(transaction))
                return _ClaimExecution(outcome=outcome, contended=False)

            token = self._token_generator.new_token()
            selected = await self._select_eligible_run(
                connection,
                command,
                platform_now=request_now,
                skip_locked=True,
            )
            contended = selected is None
            if selected is None:
                selected = await self._select_eligible_run(
                    connection,
                    command,
                    platform_now=request_now,
                    skip_locked=False,
                )
            phase = TransactionPhase.LOCK
            current_lease: WorkerLeaseRecord | None = None
            mutation_now = await self._database_now(connection)
            while selected is not None:
                run_id = RunId(type_cast(str, selected["run_id"]))
                run_status = selected["run_status"]
                current_lease = await self._load_lease_for_update(
                    connection,
                    command.tenant_id,
                    run_id,
                )
                mutation_now = await self._database_now(connection)
                if run_status == "queued" and (
                    current_lease is None
                    or current_lease.released_at is not None
                    or current_lease.lease_expires_at <= mutation_now
                ):
                    break
                contended = True
                selected = await self._select_eligible_run(
                    connection,
                    command,
                    platform_now=mutation_now,
                    skip_locked=True,
                )
                if selected is None:
                    selected = await self._select_eligible_run(
                        connection,
                        command,
                        platform_now=mutation_now,
                        skip_locked=False,
                    )
            if selected is None:
                mutation_now = await self._database_now(connection)
                receipt_record = self._no_work_receipt(command, mutation_now)
                phase = TransactionPhase.WRITE
                if not await self._insert_claim_receipt(connection, receipt_record):
                    raise _ClaimRaceLost
                await self._transaction_boundary("after_claim_receipt", connection)
                phase = TransactionPhase.COMMIT
                await execute_once(lambda: self._commit_transaction(transaction))
                return _ClaimExecution(
                    outcome=LeaseClaimOutcome.no_work(),
                    contended=contended,
                )

            run_id = RunId(type_cast(str, selected["run_id"]))
            attempt_no = (
                LeaseAttemptNo(1)
                if current_lease is None
                else type_cast(LeaseAttemptNo, current_lease.attempt_no.next())
            )
            lease_version = (
                LeaseVersion(1)
                if current_lease is None
                else type_cast(LeaseVersion, current_lease.lease_version.next())
            )
            lease = WorkerLeaseRecord(
                tenant_id=command.tenant_id,
                run_id=run_id,
                worker_id=command.worker_id,
                claim_id=command.claim_id,
                token_digest=digest_lease_token(token),
                attempt_no=attempt_no,
                lease_version=lease_version,
                duration=command.duration,
                acquired_at=mutation_now,
                heartbeat_at=mutation_now,
                lease_expires_at=mutation_now + timedelta(seconds=command.duration.value),
                released_at=None,
            )
            receipt_record = self._claimed_receipt(command, lease, token, mutation_now)
            phase = TransactionPhase.WRITE
            await self._store_lease(connection, lease, current=current_lease)
            await self._transaction_boundary("after_lease", connection)
            if not await self._insert_claim_receipt(connection, receipt_record):
                raise _ClaimRaceLost
            await self._transaction_boundary("after_claim_receipt", connection)
            phase = TransactionPhase.COMMIT
            await execute_once(lambda: self._commit_transaction(transaction))
            grant = self._grant_from_receipt(receipt_record, currently_authoritative=True)
            return _ClaimExecution(
                outcome=LeaseClaimOutcome.claimed(grant),
                contended=contended,
            )
        except _ClaimRaceLost:
            await rollback_if_active(transaction)
            raise
        except WorkerLeaseError:
            await rollback_if_active(transaction)
            raise
        except IntegrityError as error:
            await rollback_if_active(transaction)
            if _constraint_name(error) == "uq_worker_leases_tenant_claim":
                raise _ClaimRaceLost from error
            raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION) from error
        except (DBAPIError, SQLAlchemyError, ConnectionError) as error:
            rollback_confirmed = await rollback_if_active(transaction)
            if connection is not None and (
                bool(getattr(error, "connection_invalidated", False))
                or phase is TransactionPhase.COMMIT
            ):
                with suppress(Exception):
                    await connection.invalidate(error)
            disposition = classify_storage_failure(
                error,
                phase=phase,
                rollback_confirmed=rollback_confirmed,
            )
            raise worker_lease_storage_error(disposition) from error
        finally:
            if connection is not None:
                with suppress(Exception):
                    await connection.close()

    async def _replay_after_race(self, command: ClaimLeaseCommand) -> _ClaimExecution:
        try:
            async with self._engine.connect() as connection:
                platform_now = await self._database_now(connection)
                self._validate_claim_age(command.claim_id, platform_now)
                receipt = await self._load_receipt(connection, command)
                if receipt is None:
                    raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)
                outcome = await self._outcome_from_receipt(
                    connection,
                    command,
                    receipt,
                    platform_now=platform_now,
                    replayed=True,
                )
            return _ClaimExecution(outcome=outcome, contended=True)
        except WorkerLeaseError:
            raise
        except (DBAPIError, SQLAlchemyError, ConnectionError) as error:
            raise worker_lease_storage_error(StorageFailureDisposition.UNAVAILABLE) from error

    async def _database_now(self, connection: AsyncConnection) -> datetime:
        value = await connection.scalar(text("SELECT clock_timestamp()"))
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)
        return value

    @staticmethod
    def _validate_claim_age(claim_id: LeaseClaimId, platform_now: datetime) -> None:
        issued_at = claim_id_issued_at(claim_id)
        if issued_at > platform_now + timedelta(seconds=60):
            raise WorkerLeaseError(WorkerLeaseErrorCode.INVALID_INPUT)
        if issued_at + timedelta(hours=24) <= platform_now:
            raise WorkerLeaseError(WorkerLeaseErrorCode.IDEMPOTENCY_EXPIRED)

    @staticmethod
    async def _load_receipt(
        connection: AsyncConnection,
        command: ClaimLeaseCommand,
    ) -> WorkerLeaseClaimReceiptRecord | None:
        row = (
            await connection.execute(
                _receipt_select().where(
                    worker_lease_claim_receipts.c.tenant_id == str(command.tenant_id),
                    worker_lease_claim_receipts.c.claim_id == command.claim_id.value,
                )
            )
        ).first()
        if row is None:
            return None
        receipt = decode_claim_receipt(_record(row))
        stored_intent = ClaimLeaseCommand(
            tenant_id=receipt.tenant_id,
            worker_id=receipt.worker_id,
            claim_id=receipt.claim_id,
            duration=receipt.duration,
        )
        if receipt.intent_fingerprint != stored_intent.intent_fingerprint:
            raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)
        if (
            receipt.worker_id != command.worker_id
            or receipt.duration != command.duration
            or receipt.intent_fingerprint != command.intent_fingerprint
        ):
            raise WorkerLeaseError(WorkerLeaseErrorCode.IDEMPOTENCY_CONFLICT)
        return receipt

    @staticmethod
    async def _select_eligible_run(
        connection: AsyncConnection,
        command: ClaimLeaseCommand,
        *,
        platform_now: datetime,
        skip_locked: bool,
    ) -> Mapping[str, object] | None:
        eligibility = or_(
            worker_leases.c.run_id.is_(None),
            worker_leases.c.released_at.is_not(None),
            worker_leases.c.lease_expires_at <= platform_now,
        )
        statement = (
            select(runs.c.run_id, runs.c.run_status)
            .select_from(
                runs.outerjoin(
                    worker_leases,
                    and_(
                        worker_leases.c.tenant_id == runs.c.tenant_id,
                        worker_leases.c.run_id == runs.c.run_id,
                    ),
                )
            )
            .where(
                runs.c.tenant_id == str(command.tenant_id),
                runs.c.run_status == "queued",
                eligibility,
            )
            .order_by(runs.c.updated_at, runs.c.run_id)
            .limit(1)
            .with_for_update(of=runs, skip_locked=skip_locked)
        )
        row = (await connection.execute(statement)).first()
        return None if row is None else _record(row)

    @staticmethod
    async def _load_lease_for_update(
        connection: AsyncConnection,
        tenant_id: object,
        run_id: RunId,
    ) -> WorkerLeaseRecord | None:
        row = (
            await connection.execute(
                _lease_select()
                .where(
                    worker_leases.c.tenant_id == str(tenant_id),
                    worker_leases.c.run_id == str(run_id),
                )
                .with_for_update()
            )
        ).first()
        return None if row is None else decode_worker_lease(_record(row))

    @staticmethod
    async def _store_lease(
        connection: AsyncConnection,
        lease: WorkerLeaseRecord,
        *,
        current: WorkerLeaseRecord | None,
    ) -> None:
        encoded = encode_worker_lease(lease)
        if current is None:
            await connection.execute(insert(worker_leases).values(**encoded))
            return
        values = dict(encoded)
        values.pop("tenant_id")
        values.pop("run_id")
        result = await connection.execute(
            update(worker_leases)
            .where(
                worker_leases.c.tenant_id == str(lease.tenant_id),
                worker_leases.c.run_id == str(lease.run_id),
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)

    @staticmethod
    async def _insert_claim_receipt(
        connection: AsyncConnection,
        receipt: WorkerLeaseClaimReceiptRecord,
    ) -> bool:
        row = (
            await connection.execute(
                postgresql_insert(worker_lease_claim_receipts)
                .values(**encode_claim_receipt(receipt))
                .on_conflict_do_nothing(
                    index_elements=[
                        worker_lease_claim_receipts.c.tenant_id,
                        worker_lease_claim_receipts.c.claim_id,
                    ]
                )
                .returning(worker_lease_claim_receipts.c.claim_id)
            )
        ).first()
        return row is not None

    @staticmethod
    def _no_work_receipt(
        command: ClaimLeaseCommand,
        created_at: datetime,
    ) -> WorkerLeaseClaimReceiptRecord:
        issued_at = claim_id_issued_at(command.claim_id)
        return WorkerLeaseClaimReceiptRecord(
            tenant_id=command.tenant_id,
            claim_id=command.claim_id,
            claim_issued_at=issued_at,
            replay_expires_at=issued_at + timedelta(hours=24),
            worker_id=command.worker_id,
            duration=command.duration,
            intent_fingerprint=command.intent_fingerprint,
            outcome=StoredClaimOutcome.NO_WORK,
            run_id=None,
            attempt_no=None,
            initial_lease_version=None,
            lease_acquired_at=None,
            lease_expires_at=None,
            replay_token=None,
            created_at=created_at,
        )

    @staticmethod
    def _claimed_receipt(
        command: ClaimLeaseCommand,
        lease: WorkerLeaseRecord,
        token: object,
        created_at: datetime,
    ) -> WorkerLeaseClaimReceiptRecord:
        from zhiyi.domain.worker_leases.identifiers import LeaseToken

        if not isinstance(token, LeaseToken):
            raise TypeError("token must be LeaseToken")
        issued_at = claim_id_issued_at(command.claim_id)
        return WorkerLeaseClaimReceiptRecord(
            tenant_id=command.tenant_id,
            claim_id=command.claim_id,
            claim_issued_at=issued_at,
            replay_expires_at=issued_at + timedelta(hours=24),
            worker_id=command.worker_id,
            duration=command.duration,
            intent_fingerprint=command.intent_fingerprint,
            outcome=StoredClaimOutcome.CLAIMED,
            run_id=lease.run_id,
            attempt_no=lease.attempt_no,
            initial_lease_version=lease.lease_version,
            lease_acquired_at=lease.acquired_at,
            lease_expires_at=lease.lease_expires_at,
            replay_token=token,
            created_at=created_at,
        )

    async def _outcome_from_receipt(
        self,
        connection: AsyncConnection,
        command: ClaimLeaseCommand,
        receipt: WorkerLeaseClaimReceiptRecord,
        *,
        platform_now: datetime,
        replayed: bool,
    ) -> LeaseClaimOutcome:
        if receipt.outcome is StoredClaimOutcome.NO_WORK:
            return LeaseClaimOutcome.no_work(replayed=replayed)
        if receipt.run_id is None or receipt.replay_token is None:
            raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)
        authoritative = await self._receipt_is_current(
            connection,
            receipt,
            platform_now=platform_now,
        )
        grant = self._grant_from_receipt(
            receipt,
            currently_authoritative=authoritative,
        )
        return LeaseClaimOutcome.claimed(grant, replayed=replayed)

    @staticmethod
    async def _receipt_is_current(
        connection: AsyncConnection,
        receipt: WorkerLeaseClaimReceiptRecord,
        *,
        platform_now: datetime,
    ) -> bool:
        assert receipt.run_id is not None
        row = (
            await connection.execute(
                _lease_select()
                .add_columns(runs.c.run_status)
                .select_from(
                    worker_leases.join(
                        runs,
                        and_(
                            runs.c.tenant_id == worker_leases.c.tenant_id,
                            runs.c.run_id == worker_leases.c.run_id,
                        ),
                    )
                )
                .where(
                    worker_leases.c.tenant_id == str(receipt.tenant_id),
                    worker_leases.c.run_id == str(receipt.run_id),
                )
            )
        ).first()
        if row is None:
            return False
        record = _record(row)
        lease = decode_worker_lease(record)
        assert receipt.replay_token is not None
        return (
            lease.worker_id == receipt.worker_id
            and lease.claim_id == receipt.claim_id
            and lease.attempt_no == receipt.attempt_no
            and lease_token_matches(receipt.replay_token, lease.token_digest)
            and lease.released_at is None
            and lease.lease_expires_at > platform_now
            and record["run_status"] in {"queued", "running"}
        )

    @staticmethod
    def _grant_from_receipt(
        receipt: WorkerLeaseClaimReceiptRecord,
        *,
        currently_authoritative: bool,
    ) -> LeaseGrant:
        if (
            receipt.run_id is None
            or receipt.attempt_no is None
            or receipt.initial_lease_version is None
            or receipt.lease_acquired_at is None
            or receipt.lease_expires_at is None
            or receipt.replay_token is None
        ):
            raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)
        return LeaseGrant(
            tenant_id=receipt.tenant_id,
            run_id=receipt.run_id,
            worker_id=receipt.worker_id,
            claim_id=receipt.claim_id,
            token=receipt.replay_token,
            attempt_no=receipt.attempt_no,
            lease_version=receipt.initial_lease_version,
            duration=receipt.duration,
            acquired_at=receipt.lease_acquired_at,
            heartbeat_at=receipt.lease_acquired_at,
            lease_expires_at=receipt.lease_expires_at,
            renew_by_at=renew_by_at(receipt.lease_acquired_at, receipt.duration),
            currently_authoritative=currently_authoritative,
        )

    async def _transaction_boundary(self, name: str, connection: AsyncConnection) -> None:
        """Internal deterministic storage-fault seam; production is a no-op."""

    async def _commit_transaction(self, transaction: AsyncTransaction) -> None:
        """Commit once; overridden only by deterministic lost-ack tests."""

        await transaction.commit()

    async def get_authority(self, proof: LeaseAuthorityProof) -> LeaseAuthority:
        try:
            proof = self._require_authority_proof(proof)
        except WorkerLeaseError as error:
            self._observe_authority(None, error)
            raise
        try:
            await ensure_worker_lease_schema_compatible(self._engine)
            async with self._engine.connect() as connection:
                platform_now = await self._database_now(connection)
                lease, run_status = await self._load_authority_state(
                    connection,
                    proof,
                    for_update=False,
                )
                authority = self._evaluate_authority(
                    proof,
                    lease,
                    run_status,
                    platform_now=platform_now,
                )
        except WorkerLeaseError as error:
            self._observe_authority(proof, error)
            raise
        except (DBAPIError, SQLAlchemyError, ConnectionError) as error:
            public = worker_lease_storage_error(StorageFailureDisposition.UNAVAILABLE)
            self._observe_authority(proof, public)
            raise public from error
        self._observe_authority(proof, authority)
        return authority

    async def renew(self, command: RenewLeaseCommand) -> ConditionalLeaseOutcome:
        try:
            command = self._require_renew_command(command)
        except WorkerLeaseError as error:
            self._observe_conditional(LeaseOperation.RENEW, None, error)
            raise
        try:
            await ensure_worker_lease_schema_compatible(self._engine)
            result = await self._conditional_mutation(command, operation=LeaseOperation.RENEW)
        except WorkerLeaseError as error:
            self._observe_conditional(LeaseOperation.RENEW, command, error)
            raise
        self._observe_conditional(LeaseOperation.RENEW, command, result)
        return result

    async def release(self, command: ReleaseLeaseCommand) -> ConditionalLeaseOutcome:
        try:
            command = self._require_release_command(command)
        except WorkerLeaseError as error:
            self._observe_conditional(LeaseOperation.RELEASE, None, error)
            raise
        try:
            await ensure_worker_lease_schema_compatible(self._engine)
            result = await self._conditional_mutation(command, operation=LeaseOperation.RELEASE)
        except WorkerLeaseError as error:
            self._observe_conditional(LeaseOperation.RELEASE, command, error)
            raise
        self._observe_conditional(LeaseOperation.RELEASE, command, result)
        return result

    @staticmethod
    def _require_authority_proof(value: object) -> LeaseAuthorityProof:
        if not isinstance(value, LeaseAuthorityProof):
            raise WorkerLeaseError(WorkerLeaseErrorCode.INVALID_INPUT)
        return value

    @staticmethod
    def _require_renew_command(value: object) -> RenewLeaseCommand:
        if not isinstance(value, RenewLeaseCommand):
            raise WorkerLeaseError(WorkerLeaseErrorCode.INVALID_INPUT)
        return value

    @staticmethod
    def _require_release_command(value: object) -> ReleaseLeaseCommand:
        if not isinstance(value, ReleaseLeaseCommand):
            raise WorkerLeaseError(WorkerLeaseErrorCode.INVALID_INPUT)
        return value

    @staticmethod
    async def _load_authority_state(
        connection: AsyncConnection,
        proof: LeaseAuthorityProof,
        *,
        for_update: bool,
    ) -> tuple[WorkerLeaseRecord | None, str | None]:
        statement = (
            _lease_select()
            .add_columns(runs.c.run_status)
            .select_from(
                worker_leases.join(
                    runs,
                    and_(
                        runs.c.tenant_id == worker_leases.c.tenant_id,
                        runs.c.run_id == worker_leases.c.run_id,
                    ),
                )
            )
            .where(
                worker_leases.c.tenant_id == str(proof.tenant_id),
                worker_leases.c.run_id == str(proof.run_id),
            )
        )
        if for_update:
            statement = statement.with_for_update(of=worker_leases)
        row = (await connection.execute(statement)).first()
        if row is None:
            return None, None
        record = _record(row)
        run_status = record["run_status"]
        if type(run_status) is not str:
            raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)
        return decode_worker_lease(record), run_status

    @staticmethod
    def _proof_matches(proof: LeaseAuthorityProof, lease: WorkerLeaseRecord) -> bool:
        return (
            lease.tenant_id == proof.tenant_id
            and lease.run_id == proof.run_id
            and lease.worker_id == proof.worker_id
            and lease.claim_id == proof.claim_id
            and lease.attempt_no == proof.attempt_no
            and lease_token_matches(proof.token, lease.token_digest)
        )

    @classmethod
    def _evaluate_authority(
        cls,
        proof: LeaseAuthorityProof,
        lease: WorkerLeaseRecord | None,
        run_status: str | None,
        *,
        platform_now: datetime,
    ) -> LeaseAuthority:
        if lease is None or not cls._proof_matches(proof, lease):
            return LeaseAuthority.not_current()
        if lease.released_at is not None or run_status not in {"queued", "running"}:
            return cls._matched_not_current(lease)
        if lease.lease_expires_at <= platform_now:
            return LeaseAuthority.expired(
                lease_version=lease.lease_version,
                acquired_at=lease.acquired_at,
                heartbeat_at=lease.heartbeat_at,
                lease_expires_at=lease.lease_expires_at,
            )
        return LeaseAuthority.current(
            lease_version=lease.lease_version,
            acquired_at=lease.acquired_at,
            heartbeat_at=lease.heartbeat_at,
            lease_expires_at=lease.lease_expires_at,
        )

    @staticmethod
    def _matched_not_current(lease: WorkerLeaseRecord) -> LeaseAuthority:
        return LeaseAuthority(
            authoritative=False,
            reason=WorkerLeaseErrorCode.LEASE_NOT_CURRENT,
            lease_version=lease.lease_version,
            acquired_at=lease.acquired_at,
            heartbeat_at=lease.heartbeat_at,
            lease_expires_at=lease.lease_expires_at,
        )

    async def _conditional_mutation(
        self,
        command: RenewLeaseCommand | ReleaseLeaseCommand,
        *,
        operation: LeaseOperation,
    ) -> ConditionalLeaseOutcome:
        connection: AsyncConnection | None = None
        transaction: AsyncTransaction | None = None
        phase = TransactionPhase.ACQUIRE
        try:
            connection = await self._engine.connect()
            phase = TransactionPhase.BEGIN
            transaction = await connection.begin()
            await apply_transaction_settings(connection, self._settings)
            proof = command.proof
            phase = TransactionPhase.LOCK
            run_status = await connection.scalar(
                select(runs.c.run_status)
                .where(
                    runs.c.tenant_id == str(proof.tenant_id),
                    runs.c.run_id == str(proof.run_id),
                )
                .with_for_update()
            )
            if run_status is None:
                await execute_once(lambda: self._commit_transaction(transaction))
                return ConditionalLeaseOutcome(
                    applied=False,
                    authority=LeaseAuthority.not_current(),
                )
            if type(run_status) is not str:
                raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)
            lease, joined_status = await self._load_authority_state(
                connection,
                proof,
                for_update=True,
            )
            if joined_status != run_status:
                raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)
            platform_now = await self._database_now(connection)
            authority = self._evaluate_authority(
                proof,
                lease,
                run_status,
                platform_now=platform_now,
            )
            if lease is None or not self._proof_matches(proof, lease):
                await execute_once(lambda: self._commit_transaction(transaction))
                return ConditionalLeaseOutcome(applied=False, authority=authority)

            if operation is LeaseOperation.RENEW:
                if not authority.authoritative or command.expected_version != lease.lease_version:
                    await execute_once(lambda: self._commit_transaction(transaction))
                    return ConditionalLeaseOutcome(applied=False, authority=authority)
                assert isinstance(command, RenewLeaseCommand)
                next_version = type_cast(LeaseVersion, lease.lease_version.next())
                heartbeat_at = platform_now
                lease_expires_at = platform_now + timedelta(seconds=command.duration.value)
                phase = TransactionPhase.WRITE
                result = await connection.execute(
                    update(worker_leases)
                    .where(
                        worker_leases.c.tenant_id == str(proof.tenant_id),
                        worker_leases.c.run_id == str(proof.run_id),
                        worker_leases.c.lease_version == lease.lease_version.value,
                    )
                    .values(
                        duration_seconds=command.duration.value,
                        lease_version=next_version.value,
                        heartbeat_at=heartbeat_at,
                        lease_expires_at=lease_expires_at,
                    )
                )
                if result.rowcount != 1:
                    raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)
                outcome = ConditionalLeaseOutcome(
                    applied=True,
                    authority=LeaseAuthority.current(
                        lease_version=next_version,
                        acquired_at=lease.acquired_at,
                        heartbeat_at=heartbeat_at,
                        lease_expires_at=lease_expires_at,
                    ),
                    renew_by_at=renew_by_at(heartbeat_at, command.duration),
                )
            else:
                assert isinstance(command, ReleaseLeaseCommand)
                cleanup_allowed = run_status not in {"queued", "running"}
                if lease.released_at is not None:
                    await execute_once(lambda: self._commit_transaction(transaction))
                    return ConditionalLeaseOutcome(
                        applied=False,
                        authority=self._matched_not_current(lease),
                    )
                if command.expected_version != lease.lease_version or (
                    not cleanup_allowed and not authority.authoritative
                ):
                    await execute_once(lambda: self._commit_transaction(transaction))
                    return ConditionalLeaseOutcome(applied=False, authority=authority)
                next_version = type_cast(LeaseVersion, lease.lease_version.next())
                phase = TransactionPhase.WRITE
                result = await connection.execute(
                    update(worker_leases)
                    .where(
                        worker_leases.c.tenant_id == str(proof.tenant_id),
                        worker_leases.c.run_id == str(proof.run_id),
                        worker_leases.c.lease_version == lease.lease_version.value,
                    )
                    .values(
                        lease_version=next_version.value,
                        released_at=platform_now,
                    )
                )
                if result.rowcount != 1:
                    raise WorkerLeaseError(WorkerLeaseErrorCode.DATA_CORRUPTION)
                released_lease = WorkerLeaseRecord(
                    tenant_id=lease.tenant_id,
                    run_id=lease.run_id,
                    worker_id=lease.worker_id,
                    claim_id=lease.claim_id,
                    token_digest=lease.token_digest,
                    attempt_no=lease.attempt_no,
                    lease_version=next_version,
                    duration=lease.duration,
                    acquired_at=lease.acquired_at,
                    heartbeat_at=lease.heartbeat_at,
                    lease_expires_at=lease.lease_expires_at,
                    released_at=platform_now,
                )
                outcome = ConditionalLeaseOutcome(
                    applied=True,
                    authority=self._matched_not_current(released_lease),
                )
            await self._transaction_boundary("after_lease", connection)
            phase = TransactionPhase.COMMIT
            await execute_once(lambda: self._commit_transaction(transaction))
            return outcome
        except WorkerLeaseError:
            await rollback_if_active(transaction)
            raise
        except (DBAPIError, SQLAlchemyError, ConnectionError) as error:
            rollback_confirmed = await rollback_if_active(transaction)
            if connection is not None and (
                bool(getattr(error, "connection_invalidated", False))
                or phase is TransactionPhase.COMMIT
            ):
                with suppress(Exception):
                    await connection.invalidate(error)
            disposition = classify_storage_failure(
                error,
                phase=phase,
                rollback_confirmed=rollback_confirmed,
            )
            raise worker_lease_storage_error(disposition) from error
        finally:
            if connection is not None:
                with suppress(Exception):
                    await connection.close()

    def _observe_authority(
        self,
        proof: LeaseAuthorityProof | None,
        result: LeaseAuthority | WorkerLeaseError,
    ) -> None:
        if isinstance(result, WorkerLeaseError):
            outcome_code = result.code.value
            phase = (
                LeaseTransactionPhase.COMMIT
                if result.code is WorkerLeaseErrorCode.COMMIT_OUTCOME_UNKNOWN
                else LeaseTransactionPhase.COMPLETE
            )
        else:
            outcome_code = (
                "current"
                if result.authoritative
                else type_cast(WorkerLeaseErrorCode, result.reason).value
            )
            phase = LeaseTransactionPhase.COMPLETE
        deliver_terminal_observation(
            self._telemetry,
            LeaseOperationObservation(
                operation=LeaseOperation.GET_AUTHORITY,
                terminal_phase=phase,
                outcome_code=outcome_code,
                correlation_id=None,
                tenant_id=proof.tenant_id if proof is not None else None,
                run_id=proof.run_id if proof is not None else None,
                worker_id=proof.worker_id if proof is not None else None,
                claim_id=proof.claim_id if proof is not None else None,
                duration_bucket=None,
                replayed=False,
                empty=False,
                contended=False,
            ),
        )

    def _observe_conditional(
        self,
        operation: LeaseOperation,
        command: RenewLeaseCommand | ReleaseLeaseCommand | None,
        result: ConditionalLeaseOutcome | WorkerLeaseError,
    ) -> None:
        if isinstance(result, WorkerLeaseError):
            outcome_code = result.code.value
            phase = (
                LeaseTransactionPhase.COMMIT
                if result.code is WorkerLeaseErrorCode.COMMIT_OUTCOME_UNKNOWN
                else LeaseTransactionPhase.COMPLETE
            )
        else:
            outcome_code = (
                "renewed"
                if result.applied and operation is LeaseOperation.RENEW
                else "released"
                if result.applied
                else result.authority.reason.value
                if result.authority.reason is not None
                else "not_applied"
            )
            phase = LeaseTransactionPhase.COMPLETE
        proof = command.proof if command is not None else None
        deliver_terminal_observation(
            self._telemetry,
            LeaseOperationObservation(
                operation=operation,
                terminal_phase=phase,
                outcome_code=outcome_code,
                correlation_id=None,
                tenant_id=proof.tenant_id if proof is not None else None,
                run_id=proof.run_id if proof is not None else None,
                worker_id=proof.worker_id if proof is not None else None,
                claim_id=proof.claim_id if proof is not None else None,
                duration_bucket=("10-30s" if isinstance(command, RenewLeaseCommand) else None),
                replayed=False,
                empty=False,
                contended=False,
            ),
        )

    async def get_inactive_running(
        self,
        tenant_id: TenantId,
        run_id: RunId,
    ) -> InactiveRunningLease | None:
        try:
            tenant_id, run_id = self._require_inactive_identity(tenant_id, run_id)
        except WorkerLeaseError as error:
            self._observe_inactive(
                LeaseOperation.GET_INACTIVE_RUNNING,
                None,
                None,
                error,
            )
            raise
        try:
            await ensure_worker_lease_schema_compatible(self._engine)
            async with self._engine.connect() as connection:
                as_of = await self._database_now(connection)
                row = (
                    await connection.execute(
                        self._inactive_running_select()
                        .where(
                            worker_leases.c.tenant_id == str(tenant_id),
                            worker_leases.c.run_id == str(run_id),
                            self._authority_ended_at() <= as_of,
                        )
                        .limit(1)
                    )
                ).first()
            result = None if row is None else decode_inactive_running(_record(row))
        except WorkerLeaseError as error:
            self._observe_inactive(
                LeaseOperation.GET_INACTIVE_RUNNING,
                tenant_id,
                run_id,
                error,
            )
            raise
        except (DBAPIError, SQLAlchemyError, ConnectionError) as error:
            public = worker_lease_storage_error(StorageFailureDisposition.UNAVAILABLE)
            self._observe_inactive(
                LeaseOperation.GET_INACTIVE_RUNNING,
                tenant_id,
                run_id,
                public,
            )
            raise public from error
        self._observe_inactive(
            LeaseOperation.GET_INACTIVE_RUNNING,
            tenant_id,
            run_id,
            result,
        )
        return result

    async def list_inactive_running(
        self,
        tenant_id: TenantId,
        *,
        cursor: InactiveRunningCursor | None = None,
        limit: int = 100,
    ) -> InactiveRunningPage:
        try:
            tenant_id, cursor, limit = self._require_inactive_page(
                tenant_id,
                cursor,
                limit,
            )
        except WorkerLeaseError as error:
            self._observe_inactive(
                LeaseOperation.LIST_INACTIVE_RUNNING,
                None,
                None,
                error,
            )
            raise
        try:
            await ensure_worker_lease_schema_compatible(self._engine)
            async with self._engine.connect() as connection:
                as_of = (
                    cursor.as_of
                    if isinstance(cursor, InactiveRunningCursor)
                    else await self._database_now(connection)
                )
                authority_ended_at = self._authority_ended_at()
                statement = self._inactive_running_select().where(
                    worker_leases.c.tenant_id == str(tenant_id),
                    authority_ended_at <= as_of,
                )
                if cursor is not None:
                    statement = statement.where(
                        or_(
                            authority_ended_at > cursor.last_authority_ended_at,
                            and_(
                                authority_ended_at == cursor.last_authority_ended_at,
                                worker_leases.c.run_id > str(cursor.last_run_id),
                            ),
                        )
                    )
                rows = (
                    await connection.execute(
                        statement.order_by(
                            authority_ended_at,
                            worker_leases.c.run_id,
                        ).limit(limit + 1)
                    )
                ).all()
            decoded = tuple(decode_inactive_running(_record(row)) for row in rows)
            items = decoded[:limit]
            next_cursor = None
            if len(decoded) > limit:
                last = items[-1]
                next_cursor = InactiveRunningCursor(
                    tenant_id=tenant_id,
                    as_of=as_of,
                    last_authority_ended_at=last.authority_ended_at,
                    last_run_id=last.run_id,
                )
            result = InactiveRunningPage(items=items, next_cursor=next_cursor)
        except WorkerLeaseError as error:
            self._observe_inactive(
                LeaseOperation.LIST_INACTIVE_RUNNING,
                tenant_id,
                None,
                error,
            )
            raise
        except (DBAPIError, SQLAlchemyError, ConnectionError) as error:
            public = worker_lease_storage_error(StorageFailureDisposition.UNAVAILABLE)
            self._observe_inactive(
                LeaseOperation.LIST_INACTIVE_RUNNING,
                tenant_id,
                None,
                public,
            )
            raise public from error
        self._observe_inactive(
            LeaseOperation.LIST_INACTIVE_RUNNING,
            tenant_id,
            None,
            result,
        )
        return result

    @staticmethod
    def _require_inactive_identity(
        tenant_id: object,
        run_id: object,
    ) -> tuple[TenantId, RunId]:
        if not isinstance(tenant_id, TenantId) or not isinstance(run_id, RunId):
            raise WorkerLeaseError(WorkerLeaseErrorCode.INVALID_INPUT)
        return tenant_id, run_id

    @staticmethod
    def _require_inactive_page(
        tenant_id: object,
        cursor: object,
        limit: object,
    ) -> tuple[TenantId, InactiveRunningCursor | None, int]:
        if (
            not isinstance(tenant_id, TenantId)
            or (cursor is not None and not isinstance(cursor, InactiveRunningCursor))
            or (isinstance(cursor, InactiveRunningCursor) and cursor.tenant_id != tenant_id)
            or type(limit) is not int
            or not 1 <= limit <= 1_000
        ):
            raise WorkerLeaseError(WorkerLeaseErrorCode.INVALID_INPUT)
        return tenant_id, cursor, limit

    @staticmethod
    def _authority_ended_at() -> Any:
        return func.coalesce(worker_leases.c.released_at, worker_leases.c.lease_expires_at)

    @classmethod
    def _inactive_running_select(cls) -> Any:
        authority_ended_at = cls._authority_ended_at()
        return (
            select(
                worker_leases.c.tenant_id,
                worker_leases.c.run_id,
                worker_leases.c.attempt_no,
                worker_leases.c.lease_version,
                worker_leases.c.acquired_at,
                worker_leases.c.heartbeat_at,
                authority_ended_at.label("authority_ended_at"),
                case(
                    (worker_leases.c.released_at.is_not(None), "released"),
                    else_="expired",
                ).label("reason"),
            )
            .select_from(
                worker_leases.join(
                    runs,
                    and_(
                        runs.c.tenant_id == worker_leases.c.tenant_id,
                        runs.c.run_id == worker_leases.c.run_id,
                    ),
                )
            )
            .where(runs.c.run_status == "running")
        )

    def _observe_inactive(
        self,
        operation: LeaseOperation,
        tenant_id: TenantId | None,
        run_id: RunId | None,
        result: InactiveRunningLease | InactiveRunningPage | WorkerLeaseError | None,
    ) -> None:
        if isinstance(result, WorkerLeaseError):
            outcome_code = result.code.value
        elif isinstance(result, InactiveRunningPage):
            outcome_code = "listed"
        else:
            outcome_code = "found" if result is not None else "not_found"
        deliver_terminal_observation(
            self._telemetry,
            LeaseOperationObservation(
                operation=operation,
                terminal_phase=LeaseTransactionPhase.COMPLETE,
                outcome_code=outcome_code,
                correlation_id=None,
                tenant_id=tenant_id,
                run_id=run_id,
                worker_id=None,
                claim_id=None,
                duration_bucket=None,
                replayed=False,
                empty=(
                    result is None or (isinstance(result, InactiveRunningPage) and not result.items)
                ),
                contended=False,
            ),
        )
