# Read the real Windows console screen buffer, independent of redirected stdout.
# Read-Host uses WriteConsole(CONOUT$), so its actual prompts otherwise disappear
# from the installer logs. This helper neither substitutes nor answers Read-Host.
function Read-AcceptanceConsole {
    if (-not ('HardyAcceptanceConsole' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
public static class HardyAcceptanceConsole {
    [StructLayout(LayoutKind.Sequential)]
    public struct Coord { public short X; public short Y; }
    [StructLayout(LayoutKind.Sequential)]
    public struct Rect { public short Left; public short Top; public short Right; public short Bottom; }
    [StructLayout(LayoutKind.Sequential)]
    public struct Info {
        public Coord Size; public Coord Cursor; public ushort Attributes;
        public Rect Window; public Coord MaxWindow;
    }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr CreateFile(string name, uint access, uint share,
        IntPtr security, uint creation, uint flags, IntPtr template);
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern bool GetConsoleScreenBufferInfo(IntPtr console, out Info info);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool ReadConsoleOutputCharacterW(IntPtr console,
        StringBuilder output, uint length, Coord origin, out uint read);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr handle);
    public static string Read() {
        IntPtr console = CreateFile("CONOUT$", 0x80000000, 3, IntPtr.Zero, 3, 0, IntPtr.Zero);
        if (console == new IntPtr(-1)) throw new Win32Exception(Marshal.GetLastWin32Error());
        try {
            Info info;
            if (!GetConsoleScreenBufferInfo(console, out info))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            int length = checked((int)info.Size.X * (int)info.Size.Y);
            var buffer = new StringBuilder(length);
            uint read;
            if (!ReadConsoleOutputCharacterW(console, buffer, (uint)length, new Coord(), out read))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            var rows = new StringBuilder(length + info.Size.Y);
            string text = buffer.ToString();
            for (int start = 0; start < text.Length; start += info.Size.X)
                rows.AppendLine(text.Substring(start, Math.Min(info.Size.X, text.Length-start)));
            return rows.ToString();
        } finally { CloseHandle(console); }
    }
}
'@
    }
    return [HardyAcceptanceConsole]::Read()
}
