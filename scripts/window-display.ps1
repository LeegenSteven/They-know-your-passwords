# Native display metadata only; no window text, pixels or process memory.
if ('TkypDesktopWindow' -as [type]) { return }
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;
public static class TkypDesktopWindow {
    delegate bool Callback(IntPtr window, IntPtr state);
    delegate bool MonitorCallback(IntPtr monitor, IntPtr dc, ref Rect rect, IntPtr state);
    [DllImport("user32.dll")] static extern bool EnumWindows(Callback callback, IntPtr state);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint process);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr window, uint command);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr window, StringBuilder name, int size);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")] static extern bool ShowWindow(IntPtr window, int command);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr window);
    [DllImport("user32.dll")] static extern bool BringWindowToTop(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr window);
    [DllImport("user32.dll")] static extern bool IsIconic(IntPtr window);
    [DllImport("user32.dll")] static extern bool GetLayeredWindowAttributes(IntPtr window, out uint color, out byte alpha, out uint flags);
    [DllImport("user32.dll")] static extern bool SetLayeredWindowAttributes(IntPtr window, uint color, byte alpha, uint flags);
    [DllImport("user32.dll")] static extern bool GetWindowDisplayAffinity(IntPtr window, out uint affinity);
    [DllImport("user32.dll")] static extern int GetWindowLongW(IntPtr window, int index);
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("kernel32.dll")] static extern uint GetCurrentThreadId();
    [DllImport("user32.dll")] static extern bool AttachThreadInput(uint source, uint target, bool attach);
    [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr window, IntPtr after, int x, int y, int width, int height, uint flags);
    [DllImport("user32.dll")] static extern bool EnumDisplayMonitors(IntPtr dc, IntPtr clip, MonitorCallback callback, IntPtr state);
    [DllImport("user32.dll")] static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);
    [DllImport("user32.dll")] static extern IntPtr MonitorFromWindow(IntPtr window, uint flags);
    [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr window, int attribute, out int value, int size);
    struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] struct MonitorInfo { public int Size; public Rect Monitor, Work; public uint Flags; }
    static List<IntPtr> Windows(int process) {
        var found = new List<IntPtr>();
        EnumWindows(delegate(IntPtr window, IntPtr state) {
            uint owner; GetWindowThreadProcessId(window, out owner);
            if (owner != process || GetWindow(window, 4) != IntPtr.Zero) return true;
            var name = new StringBuilder(256); GetClassName(window, name, name.Capacity);
            Rect rect; GetWindowRect(window, out rect);
            if (name.ToString().EndsWith("QWindowIcon") && (IsIconic(window) ||
                (rect.Right-rect.Left >= 200 && rect.Bottom-rect.Top >= 150))) found.Add(window);
            return true;
        }, IntPtr.Zero);
        return found;
    }
    static bool OnScreen(Rect rect) {
        bool intersects = false;
        EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, delegate(IntPtr monitor, IntPtr dc, ref Rect bounds, IntPtr state) {
            var info = new MonitorInfo(); info.Size = Marshal.SizeOf(typeof(MonitorInfo)); GetMonitorInfo(monitor, ref info);
            if (Math.Min(rect.Right,info.Work.Right)-Math.Max(rect.Left,info.Work.Left) >= 100 &&
                Math.Min(rect.Bottom,info.Work.Bottom)-Math.Max(rect.Top,info.Work.Top) >= 100) intersects = true;
            return true;
        }, IntPtr.Zero);
        return intersects;
    }
    public static bool Ready(int process, bool remote) {
        foreach (var window in Windows(process)) {
            Rect rect; GetWindowRect(window, out rect); uint color, flags, affinity; byte alpha; int cloaked;
            bool layered = GetLayeredWindowAttributes(window, out color, out alpha, out flags);
            bool layeredStyle = (GetWindowLongW(window, -20) & 0x80000) != 0;
            DwmGetWindowAttribute(window, 14, out cloaked, 4);
            bool capture = GetWindowDisplayAffinity(window, out affinity) && affinity == 0;
            if (IsWindowVisible(window) && !IsIconic(window) && OnScreen(rect) && cloaked == 0 &&
                (!layeredStyle || (layered && ((flags & 2) == 0 || alpha == 255))) && (!remote || capture)) return true;
        }
        return false;
    }
    public static bool CaptureAllowed(int process) {
        foreach (var window in Windows(process)) {
            uint affinity; if (GetWindowDisplayAffinity(window, out affinity) && affinity == 0) return true;
        }
        return false;
    }
    public static void Restore(int process) {
        foreach (var window in Windows(process)) {
            ShowWindow(window, 9);
            uint color, flags; byte alpha;
            if ((GetWindowLongW(window, -20) & 0x80000) != 0) {
                if (!GetLayeredWindowAttributes(window, out color, out alpha, out flags)) { color = 0; flags = 2; alpha = 0; }
                if ((flags & 2) != 0 && alpha < 255) SetLayeredWindowAttributes(window, color, 255, flags);
            }
            Rect rect; GetWindowRect(window, out rect);
            if (!OnScreen(rect)) {
                var info = new MonitorInfo(); info.Size = Marshal.SizeOf(typeof(MonitorInfo));
                GetMonitorInfo(MonitorFromWindow(window, 2), ref info);
                int width = Math.Min(Math.Max(rect.Right-rect.Left,640), info.Work.Right-info.Work.Left);
                int height = Math.Min(Math.Max(rect.Bottom-rect.Top,480), info.Work.Bottom-info.Work.Top);
                SetWindowPos(window, IntPtr.Zero, info.Work.Left+(info.Work.Right-info.Work.Left-width)/2,
                    info.Work.Top+(info.Work.Bottom-info.Work.Top-height)/2, width, height, 0x40);
            }
            uint ignored; uint current = GetCurrentThreadId();
            uint foreground = GetWindowThreadProcessId(GetForegroundWindow(), out ignored);
            uint target = GetWindowThreadProcessId(window, out ignored);
            bool fgAttached = foreground != current && AttachThreadInput(current, foreground, true);
            bool targetAttached = target != current && target != foreground && AttachThreadInput(current, target, true);
            try { BringWindowToTop(window); SetForegroundWindow(window); }
            finally {
                if (targetAttached) AttachThreadInput(current, target, false);
                if (fgAttached) AttachThreadInput(current, foreground, false);
            }
            if ((GetWindowLongW(window, -20) & 0x80000) != 0) {
                if (!GetLayeredWindowAttributes(window, out color, out alpha, out flags)) { color = 0; flags = 2; alpha = 0; }
                if ((flags & 2) != 0 && alpha < 255) SetLayeredWindowAttributes(window, color, 255, flags);
            }
        }
    }
}
'@
