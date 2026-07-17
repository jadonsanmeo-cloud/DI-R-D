import { spawn } from 'node:child_process';
import process from 'node:process';

const [, , mode, ...args] = process.argv;
if (!mode) {
  console.error('Usage: node scripts/run-next.mjs <dev|build|build:prod|start|compile>');
  process.exit(1);
}

const env = {
  ...process.env,
  NEXT_TELEMETRY_DISABLED: '1',
  NODE_OPTIONS: '--max_old_space_size=8192',
};

if (mode === 'build:prod') {
  env.APP_ENV = 'prod';
}

async function run(command, commandArgs) {
  return await new Promise((resolve, reject) => {
    const child = spawn(command, commandArgs, {
      stdio: 'inherit',
      env,
      shell: false,
    });
    child.on('error', reject);
    child.on('exit', code => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`${command} ${commandArgs.join(' ')} exited with code ${code}`));
      }
    });
  });
}

const nextEntry = 'node_modules/next/dist/bin/next';

try {
  if (mode === 'compile') {
    await run(process.execPath, [nextEntry, 'build']);
    await run(process.execPath, [nextEntry, 'export']);
  } else if (mode === 'build:prod') {
    await run(process.execPath, [nextEntry, 'build']);
  } else {
    await run(process.execPath, [nextEntry, mode, ...args]);
  }
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}
