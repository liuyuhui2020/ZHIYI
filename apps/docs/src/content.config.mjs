import { defineCollection } from 'astro:content';
import { z } from 'astro/zod';
import { docsSchema } from '@astrojs/starlight/schema';

import { repositoryDocsLoader } from './lib/repository-docs-loader.mjs';

export const collections = {
  docs: defineCollection({
    loader: repositoryDocsLoader(),
    schema: docsSchema({
      extend: z.object({
        contentStatus: z.enum(['established', 'design-target', 'planned']),
        sourcePath: z.string().min(1),
      }),
    }),
  }),
};
