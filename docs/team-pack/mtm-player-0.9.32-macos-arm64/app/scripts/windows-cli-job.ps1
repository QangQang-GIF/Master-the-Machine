param([Parameter(Mandatory=$true)][int]$OwnerPid,
      [Parameter(Mandatory=$true)][string]$Launch)
$ErrorActionPreference = 'Stop'
# No credentials in arguments: Launch contains only executable, cwd and CLI flags.
# Assign this trusted launcher before creating the CLI, so descendants inherit the job.
try {
  Add-Type -TypeDefinition @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading.Tasks;
public static class MtmCliJob {
  [StructLayout(LayoutKind.Sequential)] struct Basic {
    public long PerProcess, PerJob; public uint Flags;
    public UIntPtr MinWorking, MaxWorking; public uint Active;
    public UIntPtr Affinity; public uint Priority, Scheduling;
  }
  [StructLayout(LayoutKind.Sequential)] struct Io {
    public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes;
  }
  [StructLayout(LayoutKind.Sequential)] struct Extended {
    public Basic Basic; public Io Io;
    public UIntPtr ProcessMemory, JobMemory, PeakProcess, PeakJob;
  }
  [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int type, ref Extended info, uint size);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
  // Intentionally retained until launcher exit; never inherited by child processes.
  static IntPtr job;
  public static int Run(int ownerPid, string executable, string cwd, string[] args) {
    using (var owner = Process.GetProcessById(ownerPid)) {
      var ownerHandle = owner.Handle; // Bind the original process, not a reused PID.
      job = CreateJobObject(IntPtr.Zero, null);
      if (job == IntPtr.Zero) throw new Exception("WINDOWS_JOB_CREATE_FAILED");
      var limits = new Extended(); limits.Basic.Flags = 0x2000; // KILL_ON_JOB_CLOSE, no breakaway.
      if (!SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf<Extended>()))
        throw new Exception("WINDOWS_JOB_CONFIGURE_FAILED");
      if (!AssignProcessToJobObject(job, Process.GetCurrentProcess().Handle))
        throw new Exception("WINDOWS_JOB_ASSIGN_FAILED");
      Task.Run(() => { owner.WaitForExit(); Environment.Exit(125); });
      var info = new ProcessStartInfo(executable) { WorkingDirectory = cwd,
        UseShellExecute = false, CreateNoWindow = true,
        RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true };
      foreach (var arg in args) info.ArgumentList.Add(arg);
      using (var child = Process.Start(info)) {
        Console.Out.WriteLine("{\"type\":\"MTM_CONTAINMENT_READY\",\"mechanism\":\"WINDOWS_JOB_OBJECT\"}");
        Console.Out.Flush();
        var input = Task.Run(async () => {
          try { await Console.OpenStandardInput().CopyToAsync(child.StandardInput.BaseStream);
            child.StandardInput.Close(); } catch (System.IO.IOException) { }
        });
        var output = child.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
        var errors = child.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());
        child.WaitForExit();
        // Descendants may retain pipe handles. Do not wait indefinitely for them.
        Task.WaitAll(new Task[] { output, errors }, 2000);
        return child.ExitCode;
      }
    }
  }
}
'@
  $spec = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Launch)) | ConvertFrom-Json
  $result = [MtmCliJob]::Run($OwnerPid, $spec.executable, $spec.cwd, [string[]]$spec.arguments)
  [Environment]::Exit($result)
} catch {
  # Only a stable code is exposed; provider errors may contain credentials.
  [Console]::Out.WriteLine('{"type":"MTM_CONTAINMENT_FAILED","code":"WINDOWS_JOB_LAUNCH_FAILED"}')
  [Environment]::Exit(126)
}
