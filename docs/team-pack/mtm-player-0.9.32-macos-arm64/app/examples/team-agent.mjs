import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const request = JSON.parse(input);
const emit = value => process.stdout.write(JSON.stringify(value) + '\n');
if (request.type === 'HEALTH') {
  emit({ protocolVersion: 1, type: 'READY', agent: 'mtm-team-agent' });
} else {
  const { eventId, runId, ticketId } = request.event;
  const base = { protocolVersion: 1, eventId, runId, ticketId };
  emit({ ...base, type: 'STARTED' });
  const content = 'Public local sample completed.\n';
  writeFileSync('evidence.txt', content);
  execFileSync('git', ['init', '--quiet'], { stdio: 'ignore' });
  execFileSync('git', ['add', 'evidence.txt'], { stdio: 'ignore' });
  execFileSync('git', ['-c', 'user.name=MTM Demo', '-c', 'user.email=demo@example.invalid',
    '-c', 'commit.gpgsign=false', 'commit', '--quiet', '--allow-empty', '-m', 'Complete public sample'], { stdio: 'ignore' });
  emit({ ...base, type: 'PROGRESS', percent: 100 });
  const revision = execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim();
  emit({ ...base, type: 'SUBMITTED', revision,
    evidence: [{ path: 'evidence.txt', sha256: createHash('sha256').update(content).digest('hex') }] });
}
