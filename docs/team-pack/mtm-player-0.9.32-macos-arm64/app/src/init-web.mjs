import { brandHeader, brandStyles } from './brand-ui.mjs';
import { randomBytes } from 'node:crypto';
import { createServer } from 'node:http';
import { resolve as resolvePath } from 'node:path';

export class WebInitError extends Error {
  constructor(code) { super(code); this.code = code; this.name = 'WebInitError'; }
}

const fail = code => { throw new WebInitError(code); };
const exact = (value, keys) => value && typeof value === 'object' && !Array.isArray(value) &&
  JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort());
const safeCode = error => typeof error?.code === 'string' && /^[A-Z0-9_]+$/.test(error.code) ?
  error.code : 'PLAYER_INIT_FAILED';
const escapeHtml = value => String(value).replace(/[&<>"']/g, character => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
})[character]);

export function page({ token, defaultApiUrl, teamPack, mode, localApp = false, selectedWorkspace = null,
  codexCliPath = '', hasSavedKey = false }) {
  const restarting = mode === 'RESTART';
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MTM PLAYER ${restarting ? 'Restart' : 'Init'}</title><style nonce="${token}">
:root{color-scheme:light;font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:#183047;background:#eaf1f6}*{box-sizing:border-box}body{min-height:100vh;margin:0;padding:32px 20px;display:grid;place-items:center;background:radial-gradient(circle at 12% 8%,#d4e7f1 0,transparent 34%),radial-gradient(circle at 90% 85%,#e0eaf5 0,transparent 36%),#f3f7fa}main{width:min(100%,760px);padding:clamp(26px,5vw,46px);background:#fff;border:1px solid #dce7ee;border-radius:22px;box-shadow:0 20px 60px #193a5518}.eyebrow{margin:0 0 9px;color:#287397;font-size:12px;font-weight:800;letter-spacing:.16em;text-transform:uppercase}h1{margin:0;font-size:clamp(28px,4vw,36px);line-height:1.15;letter-spacing:-.035em}.intro{max-width:62ch;margin:13px 0 25px;line-height:1.55}.hint{color:#627688;font-size:13px;font-weight:400}.pack{padding:18px 20px;background:#f2f8fb;border:1px solid #d7e7ef;border-radius:14px}.pack strong{display:block;margin-bottom:12px;color:#245a75;font-size:13px;letter-spacing:.04em;text-transform:uppercase}.pack-grid{display:grid;grid-template-columns:1fr 1.4fr .7fr;gap:14px}.pack p{margin:0;min-width:0;overflow-wrap:anywhere;font-size:14px;font-weight:650}.pack span{display:block;margin-bottom:4px;color:#697f8f;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}form{margin-top:26px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:19px 20px}.field{display:block;min-width:0;font-size:14px;font-weight:700}.wide{grid-column:1/-1}input:not([type=checkbox]){display:block;width:100%;margin-top:8px;padding:12px 13px;border:1px solid #cad7e0;border-radius:10px;background:#fff;color:#183047;font:inherit;font-weight:450;outline:none;transition:border-color .15s,box-shadow .15s}input:not([type=checkbox]):focus{border-color:#2f779c;box-shadow:0 0 0 3px #2f779c22}input::placeholder{color:#9aaab6}fieldset{grid-column:1/-1;margin:3px 0 0;padding:18px 19px 19px;border:1px solid #d7e2e9;border-radius:13px}legend{padding:0 7px;color:#245a75;font-size:15px;font-weight:750}fieldset .hint{margin:2px 0 15px;line-height:1.5}#model-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.model-option{display:flex;align-items:center;gap:10px;min-height:42px;margin:0;padding:8px 11px;border:1px solid #e2eaf0;border-radius:9px;background:#f9fbfd;font-size:13px;font-weight:550;cursor:pointer}.model-option:hover{border-color:#98bfd3}.model-option input{width:16px;height:16px;margin:0;accent-color:#226b90}.model-refresh-row{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:end;gap:10px;margin-bottom:15px}.model-refresh-row .field{margin:0}.model-refresh-row select{margin-bottom:0}#reload-models{min-width:0;min-height:43px;margin:0;padding:8px 13px;white-space:nowrap;background:#e9f2f7;color:#165d82;font-size:13px;box-shadow:none}#reload-models:hover:not(:disabled){background:#dcebf3}.actions{display:flex;justify-content:center;margin-top:34px}button{min-width:220px;min-height:48px;padding:12px 24px;border:0;border-radius:10px;background:#165d82;color:white;font:inherit;font-weight:750;cursor:pointer;box-shadow:0 7px 17px #165d8230}button:hover:not(:disabled){background:#104d70}button:disabled{opacity:.55;cursor:not-allowed;box-shadow:none}#status{margin-top:20px;line-height:1.5;white-space:pre-wrap;text-align:center}@media(max-width:600px){body{padding:16px}main{padding:25px 20px;border-radius:17px}.form-grid,.pack-grid,#model-options{grid-template-columns:1fr}.pack-grid{gap:11px}.actions{margin-top:30px}button{width:100%}.model-refresh-row{grid-template-columns:1fr}.model-refresh-row #reload-models{width:100%}}
.workspace-action{margin-top:-7px;padding-bottom:20px;border-bottom:1px solid #d7e2e9}.workspace-action button{min-width:0;min-height:38px;padding:8px 16px;font-size:13px;box-shadow:none;background:#e9f2f7;color:#165d82}.workspace-action button:hover:not(:disabled){background:#dcebf3}.pack[hidden]{display:none}.pack{margin-top:18px}#select-workspace{width:auto}.actions{justify-content:flex-end}.actions button{min-width:180px}main{margin:0 auto}body{align-items:start}.intro{margin-bottom:20px}select{display:block;width:100%;margin-top:8px;padding:12px;border:1px solid #cad7e0;border-radius:10px;background:#fff;color:#183047;font:inherit}.model-refresh-row{grid-template-columns:minmax(0,1fr) 150px auto}@media(max-width:600px){.model-refresh-row{grid-template-columns:1fr}}.back-management{display:inline-flex;align-items:center;margin-bottom:22px;padding:8px 12px;border:1px solid #cad7e0;border-radius:8px;color:#245a75;text-decoration:none;font-size:13px;font-weight:650}.back-management:hover{background:#edf5f9}.back-management:focus-visible{outline:3px solid #98bfd3;outline-offset:2px}${brandStyles}</style></head><body><main><div class="page-brand-heading">${brandHeader}<div class="page-heading"><h1>${restarting ? 'Restart MTM PLAYER' : 'Initialize MTM PLAYER'}</h1></div></div>${localApp ? '<a class="back-management" href="/manage">← Back to management</a>' : ''}
<p class="hint intro">${localApp ? 'Player runs locally and retains match credentials in your local user profile for recovery until the match ends or you forget it.' : restarting ? 'Enter the match details again to resume the started match. Existing source and environment files will be preserved.' : 'Match credentials are retained only in the current Player task.'}</p>
<section id="team-pack" class="pack" aria-label="Installed team pack"${localApp ? ' hidden' : ''}>
  <strong>Installed team pack</strong>
  <div class="pack-grid"><p><span>Team ID</span><b id="pack-id">${localApp ? '' : escapeHtml(teamPack.teamId)}</b></p>
  <p><span>Team name</span><b id="pack-name">${localApp ? '' : escapeHtml(teamPack.teamName)}</b></p>
  <p><span>Version</span><b id="pack-version">${localApp ? '' : escapeHtml(teamPack.version)}</b></p></div>
</section>
<form id="form">
  <div class="form-grid">
    ${localApp ? '<label class="field wide">Team workspace<input name="teamWorkspace" required placeholder="D:\\\\mtm\\\\HNKSUPER" autocomplete="off"></label><div class="wide workspace-action"><button type="button" id="select-workspace">Load team pack</button></div>' : ''}
    <label class="field">Team ID <span class="hint">(example: T01)</span><input name="teamId" required autocomplete="off"></label>
    <label class="field">Team Login Account<input name="teamLoginAccount" required autocomplete="off"></label>
    <label class="field">Password<input name="teamPassword" type="password" required autocomplete="new-password"></label>
    <label class="field">Match ID <span class="hint">(example: PPFYCZBL)</span><input name="matchId" required autocomplete="off"></label>
    ${localApp ? '' : `<label class="field wide">Team workspace <span class="hint">(contains matches and team-sdlc-pack)</span><input name="teamWorkspace" required placeholder="D:\\MTM\\T01" autocomplete="off"></label>`}
    <label class="field wide">Game Master base URL<input name="gameMasterApiUrl" type="url" required value="${escapeHtml(defaultApiUrl)}"></label>
    <label class="field wide">Team API Key<input name="gameMasterSecretKey" type="password"${hasSavedKey ? '' : ' required'} autocomplete="new-password" placeholder="${hasSavedKey ? 'Saved key — leave blank to reuse, or enter a replacement' : 'Enter your Team API Key'}"><span id="saved-key-hint" class="hint">${hasSavedKey ? 'Key saved on this computer. Leave blank to use it again.' : 'Player remembers the key after successful initialization.'}</span></label>
    ${localApp ? '<fieldset id="streaming-policy"><legend>Remote dashboard</legend><label class="model-option"><input type="checkbox" name="streamingEnabled" checked><span><b>Enable live dashboard streaming</b><small>Send the latest dashboard to the Game Server when its content changes.</small></span></label></fieldset><fieldset id="approval-policy"><legend>Approval mode</legend><div class="approval-mode-options"><label class="model-option"><input type="radio" name="approvalMode" value="auto_review" checked><span><b>Approve for me</b><small>Codex automatically reviews permission requests. Rejected or exceptional requests remain visible to the organizer.</small></span></label><label class="model-option"><input type="radio" name="approvalMode" value="user"><span><b>Manual approval</b><small>Pause the orchestrator and require the organizer to decide each permission request in MTM PLAYER.</small></span></label></div></fieldset><fieldset id="model-policy"><legend>Allowed models</legend><p class="hint">All available models are selected by default. Applies to the team orchestrator and its agents. Violations are recorded for organizer review; play continues.</p><label class="field">Codex CLI path<input id="codex-cli-path" type="text" value="' + escapeHtml(codexCliPath) + '" placeholder="C:\\...\\codex.exe" autocomplete="off"></label><p class="hint">Player fills this automatically. Change it only when a different codex.exe should be used.</p><div class="model-refresh-row"><label class="field">Orchestrator model<select id="orchestrator-model" name="orchestratorModel" required></select></label><label class="field">Reasoning effort<select id="orchestrator-effort" name="orchestratorReasoningEffort" required><option value="low">Low</option><option value="medium" selected>Medium</option><option value="high">High</option></select></label><button type="button" id="reload-models">Refresh models</button></div><p class="hint">Used to start and resume the orchestrator. Choose from the checked models below.</p><div id="model-options">Loading models from Codex host…</div></fieldset>' : ''}
  </div>
  <div class="actions"><button id="init-submit" type="submit"${localApp ? ' disabled' : ''}>${restarting ? 'Restart match' : 'Initialize'}</button></div>
</form><div id="status" role="status"></div>
<script nonce="${token}">
const form=document.getElementById('form'),status=document.getElementById('status');
${localApp ? `let modelReady=false,workspaceReady=false;
function syncOrchestratorModel(preferred){const select=document.getElementById('orchestrator-model'),current=preferred||select.value;select.replaceChildren();for(const input of document.querySelectorAll('input[name=allowedModels]:checked')){const option=document.createElement('option');option.value=input.value;option.textContent=input.value;select.append(option)}if(Array.from(select.options).some(o=>o.value===current))select.value=current;document.getElementById('init-submit').disabled=!workspaceReady||!modelReady||!select.value;}
async function loadModels(){modelReady=false;document.getElementById('init-submit').disabled=true;const box=document.getElementById('model-options'),reload=document.getElementById('reload-models'),path=document.getElementById('codex-cli-path'),manual=path.value.trim();reload.disabled=true;reload.textContent='Refreshing…';box.textContent='Loading models from Codex host…';try{const r=await fetch(location.pathname+'/models',{method:manual?'POST':'GET',headers:manual?{'content-type':'application/json'}:{},body:manual?JSON.stringify({codexCliPath:manual}):undefined,cache:'no-store'}),catalog=await r.json();if(!r.ok||!catalog.models?.length)throw Error(catalog.code||'MODEL_CATALOG_UNAVAILABLE');if(catalog.codexCliPath)path.value=catalog.codexCliPath;box.replaceChildren();for(const model of catalog.models){const label=document.createElement('label'),input=document.createElement('input');label.className='model-option';input.type='checkbox';input.name='allowedModels';input.value=model.id;input.checked=catalog.selected?catalog.selected.includes(model.id):true;label.append(input,document.createTextNode(model.name+' ('+model.id+')'));input.onchange=()=>syncOrchestratorModel();box.append(label)}const approval=document.querySelector('input[name=approvalMode][value="'+(catalog.approvalMode||'auto_review')+'"]');if(approval)approval.checked=true;const effort=document.getElementById('orchestrator-effort');if(catalog.orchestratorReasoningEffort)effort.value=catalog.orchestratorReasoningEffort;modelReady=true;syncOrchestratorModel(catalog.orchestratorModel);document.getElementById('init-submit').disabled=!workspaceReady}catch(error){box.textContent=error.message==='CODEX_CLI_NOT_FOUND'?'Codex CLI was not found. Paste the full path to codex.exe above and select Refresh models.':error.message==='INVALID_CODEX_CLI_PATH'?'Enter the full absolute path to codex.exe.':error.message==='MODEL_CATALOG_TIMEOUT'?'Codex model discovery timed out. Check the Codex login and network, then try again.':'Could not read the Codex model list ('+error.message+'). No model names were guessed.'}finally{reload.disabled=false;reload.textContent='Refresh models'}}
document.getElementById('reload-models').onclick=loadModels;loadModels();
let workspacePickerOpen=false;
async function browseWorkspace(){if(workspacePickerOpen)return;workspacePickerOpen=true;try{status.textContent='Choose a team workspace in the Windows folder dialog…';const r=await fetch('/manage/browse-workspace',{method:'POST',headers:{'content-type':'application/json'},body:'{}'}),v=await r.json();if(!r.ok)throw Error(v.code);if(v.workspace){form.elements.teamWorkspace.value=v.workspace;form.elements.teamWorkspace.oninput();document.getElementById('select-workspace').click();}else status.textContent='Folder selection cancelled.';}catch(error){status.textContent=error.message;}finally{workspacePickerOpen=false;}}
form.elements.teamWorkspace.onclick=browseWorkspace;
form.elements.teamWorkspace.onkeydown=event=>{if(event.key==='F4'||(event.altKey&&event.key==='ArrowDown')){event.preventDefault();browseWorkspace();}};
form.elements.teamWorkspace.title='Click to choose a folder. You can also type a path or press F4.';
form.elements.teamWorkspace.oninput=()=>{workspaceReady=false;document.getElementById('team-pack').hidden=true;document.getElementById('init-submit').disabled=true;};
document.getElementById('select-workspace').onclick=async()=>{workspaceReady=false;document.getElementById('team-pack').hidden=true;document.getElementById('init-submit').disabled=true;try{const r=await fetch('/init/workspace',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({workspace:form.elements.teamWorkspace.value})}),v=await r.json();if(!r.ok){if(v.code==='ACTIVE_SESSION_STOP_CONFIRMATION_REQUIRED'&&confirm('Stop the current team orchestrator before changing workspace?')){const stopped=await fetch('/manage/stop-orchestrator',{method:'POST',headers:{'content-type':'application/json'},body:'{}'}),receipt=await stopped.json();if(receipt.status==='ORCHESTRATOR_STOPPED'){document.getElementById('select-workspace').click();return;}throw Error(receipt.code||receipt.status);}throw Error(v.code);}form.elements.teamWorkspace.value=v.workspace;form.elements.teamId.value=v.teamPack.teamId;form.elements.gameMasterSecretKey.required=!v.hasSavedSecret;form.elements.gameMasterSecretKey.placeholder=v.hasSavedSecret?'Saved key — leave blank to reuse, or enter a replacement':'Enter your Team API Key';document.getElementById('saved-key-hint').textContent=v.hasSavedSecret?'Key saved on this computer. Leave blank to use it again.':'Player remembers the key after successful initialization.';document.getElementById('pack-id').textContent=v.teamPack.teamId;document.getElementById('pack-name').textContent=v.teamPack.teamName;document.getElementById('pack-version').textContent=v.teamPack.version;document.querySelector('.workspace-action').after(document.getElementById('team-pack'));document.getElementById('team-pack').classList.add('wide');document.getElementById('team-pack').hidden=false;workspaceReady=true;document.getElementById('init-submit').disabled=!modelReady;status.textContent='Team pack verified.'}catch(error){status.textContent=error.message==='ACTIVE_SESSION_STOP_CONFIRMATION_REQUIRED'?'Stop the current orchestrator from Manage before switching workspace.':error.message}};
const selectedWorkspace=${JSON.stringify(selectedWorkspace).replace(/</g, '\\u003c')};if(selectedWorkspace){form.elements.teamWorkspace.value=selectedWorkspace;document.getElementById('select-workspace').click();}
` : ''}
const errors={GAME_MASTER_KEY_REQUIRED:'Enter a Team API Key for this server.',INVALID_MATCH_ID:'Invalid Match ID.',INVALID_GAME_MASTER_API_URL:'Invalid Game Master base URL.',GAME_MASTER_VALIDATION_FAILED:'Game Master rejected the Team ID or Match ID.',INVALID_TEAM_WORKSPACE:'Invalid team workspace.',TEAM_WORKSPACE_MISMATCH:'The team workspace must match the workspace used to open the Player task.',CHALLENGE_NOT_EMPTY:'The match checkout is not empty.',CHALLENGE_RESET_FAILED:'Could not clear the selected match checkout.',TEAM_PACK_TEAM_MISMATCH:'The Team ID does not match the installed team pack.',TEAM_PACK_CHANGED:'The installed team pack changed after this page was opened.',INVALID_TEAM_WORKSPACE_LAYOUT:'The team workspace does not contain required matches and team-sdlc-pack folders.',TEAM_WORKSPACE_PREPARATION_FAILED:'Could not create the team workspace layout.',TEAM_WORKSPACE_PREPARED:'The matches and team-sdlc-pack folders were created. Copy team-sdlc-pack-<version>.tgz and install.ps1 into team-sdlc-pack, run the installer, then open the contest task from the team workspace.',LINKED_TEAM_WORKSPACE:'The team workspace must not be a symbolic link.',TEAM_RUNTIME_NOT_INSTALLED:'The team runtime is not installed. Run team-sdlc-pack\\install.ps1 before opening the contest task.',INVALID_TEAM_PACK:'Invalid team-sdlc-pack.',INVALID_TEAM_PACK_INFO:'Invalid team pack identity information.',INSTALLED_RUNTIME_TAMPERED:'The installed team runtime failed its integrity check.'};
const sendInit=data=>fetch(location.pathname+'/submit',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(data)}).then(async response=>({response,result:await response.json()}));
form.addEventListener('submit',async event=>{event.preventDefault();status.textContent='Retrieving team information and verifying team-sdlc-pack…';
const data=Object.fromEntries(new FormData(form).entries());
${localApp ? "data.allowedModels=new FormData(form).getAll('allowedModels');data.streamingEnabled=form.elements.streamingEnabled.checked;if(!workspaceReady||!modelReady||!data.allowedModels.length){status.textContent='Select at least one allowed model.';return;}" : ''}
try{let {result}=await sendInit(data);
if(result.code==='CHALLENGE_NOT_EMPTY'){
const challengePath=data.teamWorkspace.replace(/[\\\\/]+$/,'')+'\\\\matches\\\\'+data.matchId;
if(!confirm('The match checkout is not empty:\\n\\n'+challengePath+'\\n\\nDelete all contents in this folder and continue? This cannot be undone.')){status.textContent='Initialization cancelled. Challenge files were preserved.';return;}
status.textContent='Clearing the selected match checkout and initializing…';
({result}=await sendInit({...data,clearChallenge:true}));
}
${localApp && !restarting ? `if(result.code==='STOP_AGENTS_BEFORE_NEW_ROUND'){
if(!confirm('An existing round is still marked active. Stop its Player-managed runtime before initializing a new round?')){status.textContent='Initialization cancelled. Existing round preserved.';return;}
status.textContent='Stopping the existing round…';
const stopped=await fetch('/init/stop-existing',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({stopExisting:true})});const receipt=await stopped.json();
status.textContent=receipt.status==='INIT_FORM_READY'?'Existing round stopped. Click Initialize to continue.':('Could not stop the existing round: '+(receipt.code||receipt.status));return;
}` : ''}
if(!result.ok){status.textContent=(result.code==='TEAM_WORKSPACE_PREPARED'?'':'Could not ${restarting ? 'restart' : 'initialize'}: ')+(errors[result.code]||result.code);return;}
form.hidden=true;
status.textContent='${restarting ? 'RESTART_READY' : 'READY'}\\nMatch: '+result.matchId+'\\nTeam: '+result.team.id+' — '+result.team.name+'\\nTeam runtime: '+result.teamRuntime.version+' (verified)\\nOpening dashboard…';
if(result.dashboardUrl){const link=document.createElement('a');link.href=result.dashboardUrl;link.textContent='Open match dashboard';link.style.display='block';link.style.marginTop='12px';status.append(link);location.replace(result.dashboardUrl);}
}catch{status.textContent='Could not connect to MTM PLAYER.';}});
</script></main></body></html>`;
}

function readBody(request, maxBytes = 64 * 1024) {
  return new Promise((resolve, reject) => {
    const chunks = []; let size = 0;
    request.on('data', chunk => {
      size += chunk.length;
      if (size > maxBytes) { reject(new WebInitError('WEB_INIT_REQUEST_TOO_LARGE')); request.destroy(); }
      else chunks.push(chunk);
    });
    request.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    request.on('error', () => reject(new WebInitError('WEB_INIT_REQUEST_FAILED')));
  });
}

export function initializeMatchInBrowser({ reconfigure = false, expectedTeamWorkspace, teamPack,
  mode = 'INIT' } = {}, {
  configStore, credentialStore, initializeMatch, defaultApiUrl, onReady = () => {},
  onInitialized = async () => null,
  fetchImpl = globalThis.fetch, testMode = false, playerRoot, verifyTeamRuntime,
  timeoutMs = 10 * 60 * 1000
} = {}) {
  if (!configStore || !credentialStore || typeof initializeMatch !== 'function' ||
      typeof onReady !== 'function' || typeof expectedTeamWorkspace !== 'string' ||
      typeof onInitialized !== 'function' ||
      !['INIT', 'RESTART'].includes(mode) ||
      !exact(teamPack, ['version', 'teamId', 'teamName']) ||
      !['version', 'teamId', 'teamName'].every(key => typeof teamPack[key] === 'string' && teamPack[key].length > 0)) {
    fail('INVALID_WEB_INITIALIZER');
  }
  const configured = configStore.load();
  const token = randomBytes(24).toString('hex');
  const route = `/${token}`;

  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      // Browsers may retain the GET connection; close it so the one-shot CLI can exit.
      if (server.listening) {
        server.close();
        // Closing idle keep-alive sockets lets the submitted response finish before
        // the CLI exits; closeAllConnections can truncate the browser's JSON read.
        server.closeIdleConnections?.();
      }
      if (error) reject(error); else resolve(value);
    };
    const server = createServer(async (request, response) => {
      const expectedHost = `127.0.0.1:${server.address().port}`;
      response.setHeader('cache-control', 'no-store');
      response.setHeader('referrer-policy', 'no-referrer');
      response.setHeader('x-content-type-options', 'nosniff');
      if (request.headers.host !== expectedHost) { response.writeHead(400).end(); return; }
      if (request.method === 'GET' && request.url === route) {
        const html = page({ token, teamPack, mode,
          defaultApiUrl: configured?.gameMasterApiUrl ?? defaultApiUrl });
        response.writeHead(200, { 'content-type': 'text/html; charset=utf-8',
          'content-security-policy': `default-src 'none'; img-src data:; style-src 'nonce-${token}'; script-src 'nonce-${token}'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'` });
        response.end(html); return;
      }
      if (request.method !== 'POST' || request.url !== `${route}/submit` ||
          !/^application\/json(?:;|$)/i.test(request.headers['content-type'] ?? '') ||
          (request.headers.origin && request.headers.origin !== `http://${expectedHost}`)) {
        response.writeHead(404).end(); return;
      }
      try {
        const input = JSON.parse(await readBody(request));
        const expectedKeys = ['teamId', 'matchId', 'teamWorkspace', 'gameMasterApiUrl',
          'gameMasterSecretKey', 'teamLoginAccount', 'teamPassword'];
        const clearChallenge = input.clearChallenge ?? false;
        if (typeof clearChallenge !== 'boolean' ||
            (!exact(input, expectedKeys) && !exact(input, [...expectedKeys, 'clearChallenge']))) {
          fail('INVALID_WEB_INIT_FORM');
        }
        if (resolvePath(input.teamWorkspace).toLowerCase() !== resolvePath(expectedTeamWorkspace).toLowerCase()) {
          fail('TEAM_WORKSPACE_MISMATCH');
        }
        const apiUrlChanged = Boolean(configured) &&
          input.gameMasterApiUrl !== configured.gameMasterApiUrl;
        const session = await initializeMatch({ ...input,
          gameMasterSecretKey: input.gameMasterSecretKey,
          clearChallenge: mode === 'RESTART' ? false : clearChallenge,
          gameMasterApiUrl: configured && !apiUrlChanged && !reconfigure ? undefined :
            input.gameMasterApiUrl,
          reconfigure: reconfigure || apiUrlChanged }, {
          configStore, credentialStore, fetchImpl, testMode, playerRoot, verifyTeamRuntime
        });
        if (session.teamRuntime.runtime.version !== teamPack.version ||
            session.teamRuntime.runtime.teamId !== teamPack.teamId ||
            session.teamRuntime.runtime.teamName !== teamPack.teamName) fail('TEAM_PACK_CHANGED');
        const initialized = await onInitialized(session);
        const initializedShape = initialized === null || exact(initialized, ['dashboardUrl']) ||
          (mode === 'RESTART' && exact(initialized, ['dashboardUrl', 'playerSessionId']));
        if (!initializedShape || (initialized !== null &&
            (typeof initialized.dashboardUrl !== 'string' ||
              !/^http:\/\/127\.0\.0\.1:\d+\/[A-Za-z0-9]+$/.test(initialized.dashboardUrl) ||
              (Object.hasOwn(initialized, 'playerSessionId') &&
                (typeof initialized.playerSessionId !== 'string' ||
                  !/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/.test(initialized.playerSessionId)))))) {
          fail('INVALID_DASHBOARD_CONTINUATION');
        }
        const publicResult = { ok: true, status: session.status,
          matchId: session.matchId,
          team: { id: session.teamId, name: session.teamName },
          playerSessionId: initialized?.playerSessionId ?? session.playerSessionId,
          teamWorkspace: session.teamWorkspace,
          teamRuntime: { action: session.teamRuntime.action,
            version: session.teamRuntime.runtime.version,
            rootAgentType: session.teamRuntime.runtime.rootAgentType } };
        if (initialized) publicResult.dashboardUrl = initialized.dashboardUrl;
        response.writeHead(200, { 'content-type': 'application/json', connection: 'close' });
        response.end(JSON.stringify(publicResult), () => {
          // Keep the one-shot form origin alive briefly so the browser can complete
          // its immediate cross-port navigation to the dashboard service.
          setTimeout(() => finish(null, session), 1_000).unref?.();
        });
      } catch (error) {
        response.writeHead(400, { 'content-type': 'application/json' });
        response.end(JSON.stringify({ ok: false, code: safeCode(error) }));
      }
    });
    server.on('clientError', (_error, socket) => socket.destroy());
    server.on('error', () => finish(new WebInitError('WEB_INIT_SERVER_FAILED')));
    const timer = setTimeout(() => finish(new WebInitError('WEB_INIT_TIMEOUT')), timeoutMs);
    server.listen(0, '127.0.0.1', () => {
      const url = `http://127.0.0.1:${server.address().port}${route}`;
      try { onReady(url); } catch { finish(new WebInitError('WEB_INIT_OPEN_FAILED')); }
    });
  });
}
