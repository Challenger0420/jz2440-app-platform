using System;

// Shared by the legacy RunBoard bridge and JZ2440 Control.  The adapter only
// describes the board-side lifecycle and RB1 wire markers; it never owns a
// serial port.
public sealed class RunBoardAdapter : IApplicationAdapter
{
    public string Name { get { return "runboard"; } }
    public string StartCommand { get { return "/opt/jz2440/bin/appctl start runboard\n"; } }
    public string StopCommand { get { return "<RBQUIT>\n"; } }
    public bool IsApplicationTraffic(string line) { return line != null && line.StartsWith("RB1|", StringComparison.Ordinal); }
    public bool IsReady(string line) { return line != null && line.IndexOf("<APPREADY|runboard>", StringComparison.Ordinal) >= 0; }
    public bool IsStopped(string line) { int code; return TryGetReturnCode(line, out code); }

    public bool TryGetReturnCode(string line, out int code)
    {
        const string prefix = "<APPSTOP|runboard|RC=";
        code = -1;
        if (line == null) return false;
        string normalized = line.TrimEnd('\r', '\n');
        if (!normalized.StartsWith(prefix, StringComparison.Ordinal) || !normalized.EndsWith(">", StringComparison.Ordinal)) return false;
        string value = normalized.Substring(prefix.Length, normalized.Length - prefix.Length - 1);
        return Int32.TryParse(value, out code) && code >= 0;
    }
}
