import { lstatSync, readFileSync, readdirSync } from 'node:fs';
import { builtinModules } from 'node:module';
import { dirname, resolve, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

const ownRoot = fileURLToPath(new URL('..', import.meta.url));
const allowed = ['src/', 'scripts/', 'examples/', 'docs/protocol.md', 'docs/local-application.md',
  'docs/installation.md', 'docs/host-control-plane.md', 'README.md', 'assets/'];
const blocked = /(?:organizer|mtm-master|grader|hidden[-_]?tests?|answer[-_]?keys?|oracle[-_]?patch|score[-_]?manifest)/i;

/** Check the package allowlist, file paths and static module dependency boundary. */
export function scanDistribution(root = ownRoot) {
  root = resolve(root);
  const rejectLinks = absolute => {
    let current = absolute;
    while (true) {
      if (lstatSync(current).isSymbolicLink()) throw new Error('Linked distribution path');
      const parent = dirname(current);
      if (parent === current) break;
      current = parent;
    }
  };
  rejectLinks(resolve(root, 'package.json'));
  const pkg = JSON.parse(readFileSync(resolve(root, 'package.json'), 'utf8'));
  if (JSON.stringify(pkg.files) !== JSON.stringify(allowed) ||
      ['dependencies', 'optionalDependencies', 'peerDependencies', 'bundledDependencies'].some(k => pkg[k] && Object.keys(pkg[k]).length)) {
    throw new Error('Unexpected package boundary');
  }
  const paths = ['package.json'];
  const visit = path => {
    const absolute = resolve(root, path);
    rejectLinks(absolute);
    const stat = lstatSync(absolute);
    if (stat.isSymbolicLink() || blocked.test(path)) throw new Error('Forbidden distribution path');
    if (stat.isDirectory()) {
      for (const entry of readdirSync(absolute)) visit(`${path.replace(/\/$/, '')}/${entry}`);
    } else {
      if (!stat.isFile() || (!/\.(?:mjs|json|md)$/.test(path) &&
          path !== 'assets/master-the-machine-lockup.png' && path !== 'assets/supervisor-logo.png' && path !== 'scripts/windows-cli-job.ps1')) throw new Error('Unexpected distribution file');
      paths.push(path);
    }
  };
  for (const path of allowed) visit(path);
  for (const path of paths.filter(path => path.endsWith('.mjs'))) {
    const source = readFileSync(resolve(root, path), 'utf8');
    for (const match of source.matchAll(/(?:from\s*|import\s*\(\s*|import\s*)['"]([^'"]+)['"]/g)) {
      const target = match[1];
      if (target.startsWith('node:') && builtinModules.includes(target.slice(5))) continue;
      const local = relative(root, resolve(root, dirname(path), target)).split(sep).join('/');
      if (!target.startsWith('.') || blocked.test(target) || !paths.includes(local)) throw new Error('External dependency');
    }
  }
  return paths.sort();
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const paths = scanDistribution();
  console.log(JSON.stringify({ status: 'PASS', files: paths }, null, 2));
}
