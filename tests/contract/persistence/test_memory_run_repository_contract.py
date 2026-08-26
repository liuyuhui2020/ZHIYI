from run_repository_contract import RunRepositoryContract

from zhiyi.adapters.persistence.memory_run_repository import MemoryRunRepository


class TestMemoryRunRepositoryContract(RunRepositoryContract):
    repository_factory = MemoryRunRepository
