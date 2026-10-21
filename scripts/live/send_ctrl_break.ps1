<#
  Helper for stop_live.ps1 (P12): send CTRL+BREAK to the console of -TargetPid. Run as its own process: it gives up
  its own console to attach to the target's, and ignores the event itself. Exit 0 sent, 1 cannot attach, 2 not sent.
#>
param([int]$TargetPid = 0)
if ($TargetPid -le 0) { exit 2 }
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class D128ConsoleBreak {
    public delegate bool Handler(uint ctrlType);
    static readonly Handler Ignore = delegate (uint ctrlType) { return true; };
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool AttachConsole(uint pid);
    [DllImport("kernel32.dll")] static extern bool FreeConsole();
    [DllImport("kernel32.dll")] static extern bool SetConsoleCtrlHandler(Handler h, bool add);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool GenerateConsoleCtrlEvent(uint ev, uint group);
    public static int Send(uint pid) {
        FreeConsole();
        if (!AttachConsole(pid)) { return 1; }
        SetConsoleCtrlHandler(Ignore, true);
        bool ok = GenerateConsoleCtrlEvent(1, 0);
        System.Threading.Thread.Sleep(1000);
        FreeConsole();
        return ok ? 0 : 2;
    }
}
'@
exit [D128ConsoleBreak]::Send([uint32]$TargetPid)
