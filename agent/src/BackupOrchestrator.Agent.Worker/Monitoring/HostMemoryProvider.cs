using System.Runtime.InteropServices;
using BackupOrchestrator.Agent.Core.Contracts;
using BackupOrchestrator.Agent.Core.Models;

namespace BackupOrchestrator.Agent.Worker.Monitoring;

/// <summary>P/Invoke wrapper over the Win32 GlobalMemoryStatusEx API.</summary>
public sealed class HostMemoryProvider : IHostMemoryProvider
{
    [StructLayout(LayoutKind.Sequential)]
    private struct MEMORYSTATUSEX
    {
        public uint dwLength;
        public uint dwMemoryLoad;
        public ulong ullTotalPhys;
        public ulong ullAvailPhys;
        public ulong ullTotalPageFile;
        public ulong ullAvailPageFile;
        public ulong ullTotalVirtual;
        public ulong ullAvailVirtual;
        public ulong ullAvailExtendedVirtual;
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GlobalMemoryStatusEx(ref MEMORYSTATUSEX lpBuffer);

    public MemoryStatus GetMemoryStatus()
    {
        var status = new MEMORYSTATUSEX
        {
            // Required by the Win32 API contract -- the call fails if this
            // isn't set correctly before invocation.
            dwLength = (uint)Marshal.SizeOf<MEMORYSTATUSEX>(),
        };

        if (!GlobalMemoryStatusEx(ref status))
        {
            // Don't silently return zeros -- let the caller's try/catch
            // handle this as a metrics-collection failure (Metrics: null).
            throw new InvalidOperationException(
                $"GlobalMemoryStatusEx failed with Win32 error {Marshal.GetLastWin32Error()}");
        }

        return new MemoryStatus
        {
            UsedBytes = (long)(status.ullTotalPhys - status.ullAvailPhys),
            TotalBytes = (long)status.ullTotalPhys,
        };
    }
}
