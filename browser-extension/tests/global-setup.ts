import type { FullConfig } from '@playwright/test';
import fs from 'fs';

const DEST = 'extension/tests';

export default async function globalSetup(config: FullConfig) {
  // Create a temporary directory and copy tests/* to extension/tests
  fs.existsSync(DEST);
  fs.cpSync('./tests', DEST, { recursive: true });
}
