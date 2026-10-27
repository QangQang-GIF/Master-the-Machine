import { spawn } from 'node:child_process';

// A native dialog selects a path only; it never uploads or initializes a match.
export function browseTeamWorkspace() {
  if (process.platform !== 'win32') return Promise.reject(new Error('FOLDER_PICKER_WINDOWS_ONLY'));
  return new Promise((resolve, reject) => {
    const script = `Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'MTM PLAYER: select the installed team workspace'
$dialog.ShowNewFolderButton = $false
$owner = New-Object System.Windows.Forms.Form
$owner.Text = 'MTM PLAYER — Select workspace'
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
$owner.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::FixedToolWindow
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.Opacity = 0
try {
  $owner.Show()
  $owner.Activate()
  $owner.BringToFront()
  if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.SelectedPath) }
} finally {
  $dialog.Dispose()
  $owner.Close()
  $owner.Dispose()
}`;
    const child = spawn('powershell.exe', ['-NoLogo','-NoProfile','-STA','-EncodedCommand',Buffer.from(script,'utf16le').toString('base64')], { windowsHide:true, shell:false, stdio:['ignore','pipe','pipe'] });
    let output=''; let settled=false;
    const finish=(error,value)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(value)};
    const timer=setTimeout(()=>{child.kill();finish(new Error('FOLDER_PICKER_TIMEOUT'))},120000);
    child.stdout.on('data',chunk=>{output+=chunk.toString();if(output.length>32768){child.kill();finish(new Error('FOLDER_PICKER_FAILED'))}});
    child.stderr.resume();
    child.once('error',()=>finish(new Error('FOLDER_PICKER_FAILED')));
    child.once('exit',code=>code===0?finish(null,output.trim()||null):finish(new Error('FOLDER_PICKER_FAILED')));
  });
}
