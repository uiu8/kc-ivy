// Test only: preserve exit codes and normalize Windows CLI line endings to POSIX.
// SQL is still executed by the actual SQLite 3.26 binary, never emulated.
import {spawnSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
const r=spawnSync(process.env.KC_SQLITE_CLI || 'D:/software/KC-development/sqlite-3.26.0/tools/sqlite-tools-win32-x86-3260000/sqlite3.exe',process.argv.slice(2),{input:readFileSync(0),maxBuffer:80*1024*1024});
process.stdout.write((r.stdout||Buffer.alloc(0)).toString('utf8').replaceAll('\r\n','\n'));
process.stderr.write(r.stderr||'');
process.exit(r.status??1);
