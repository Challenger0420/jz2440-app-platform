using System;
using System.Collections.Generic;
using System.Text;
using System.Threading;

public enum BoardControllerMode
{
    Unknown,
    Console,
    Application
}

public sealed class BoardController
{
    private readonly ISerialTransport serial;
    private readonly IApplicationAdapter application;
    private readonly StringBuilder pending = new StringBuilder();

    public BoardControllerMode Mode { get; private set; }
    public string LastApplicationStopLine { get; private set; }

    // The caller may use this only after independently confirming that the
    // board is at the interactive serial shell. UNKNOWN remains fail-closed.
    public void ConfirmConsoleMode()
    {
        Mode = BoardControllerMode.Console;
    }

    public void ConfirmApplicationMode()
    {
        Mode = BoardControllerMode.Application;
    }

    public BoardController(ISerialTransport transport, IApplicationAdapter adapter)
    {
        serial = transport;
        application = adapter;
        Mode = BoardControllerMode.Unknown;
    }

    public BoardControllerMode Observe(int milliseconds)
    {
        DateTime deadline = DateTime.UtcNow.AddMilliseconds(milliseconds);
        while (DateTime.UtcNow < deadline)
        {
            IList<string> lines = ReadLines(serial.ReadAvailable());
            foreach (string line in lines)
            {
                if (application.IsApplicationTraffic(line)) Mode = BoardControllerMode.Application;
                else if (!application.IsReady(line) && !application.IsStopped(line) && line.Trim().Length != 0 &&
                         Mode != BoardControllerMode.Application)
                    Mode = BoardControllerMode.Console;
            }
            if (Mode == BoardControllerMode.Application) return Mode;
            Thread.Sleep(25);
        }
        return Mode;
    }

    public bool SyncConsole(string marker, int timeoutMilliseconds)
    {
        if (string.IsNullOrEmpty(marker)) throw new ArgumentException("marker");
        serial.Write("echo " + marker + "\n");
        DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMilliseconds);
        while (DateTime.UtcNow < deadline)
        {
            IList<string> lines = ReadLines(serial.ReadAvailable());
            foreach (string line in lines)
                if (line.IndexOf(marker, StringComparison.Ordinal) >= 0)
                {
                    Mode = BoardControllerMode.Console;
                    return true;
                }
            Thread.Sleep(25);
        }
        return false;
    }

    public bool StartApplication(int timeoutMilliseconds)
    {
        LastApplicationStopLine = null;
        if (Mode == BoardControllerMode.Unknown) Observe(750);
        if (Mode == BoardControllerMode.Application) return true;
        if (Mode != BoardControllerMode.Console) return false;
        string marker = "JZ2440_CTL_SYNC_" + DateTime.UtcNow.Ticks.ToString();
        if (!SyncConsole(marker, timeoutMilliseconds)) return false;
        serial.Write(application.StartCommand);
        DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMilliseconds);
        while (DateTime.UtcNow < deadline)
        {
            IList<string> lines = ReadLines(serial.ReadAvailable());
            for (int index = 0; index < lines.Count; index++)
            {
                string line = lines[index];
                if (application.IsReady(line) || application.IsApplicationTraffic(line))
                {
                    for (int remaining = index + 1; remaining < lines.Count; remaining++)
                        pending.Append(lines[remaining]);
                    Mode = BoardControllerMode.Application;
                    return true;
                }
            }
            Thread.Sleep(25);
        }
        return false;
    }

    public string TakePendingApplicationData()
    {
        string value = pending.ToString();
        pending.Length = 0;
        return value;
    }

    public bool StopApplication(int timeoutMilliseconds)
    {
        if (Mode == BoardControllerMode.Console) return true;
        if (Mode == BoardControllerMode.Unknown) Observe(750);
        if (Mode == BoardControllerMode.Console) return true;
        if (Mode != BoardControllerMode.Application) return false;
        serial.Write(application.StopCommand);
        DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMilliseconds);
        bool stopped = false;
        while (DateTime.UtcNow < deadline)
        {
            IList<string> lines = ReadLines(serial.ReadAvailable());
            foreach (string line in lines)
                if (application.IsStopped(line)) { LastApplicationStopLine = line; stopped = true; break; }
            if (stopped) break;
            Thread.Sleep(25);
        }
        if (!stopped) return false;
        Mode = BoardControllerMode.Console;
        string marker = "JZ2440_CTL_SYNC_" + DateTime.UtcNow.Ticks.ToString();
        return SyncConsole(marker, timeoutMilliseconds);
    }

    public IList<string> ListApplications(int timeoutMilliseconds)
    {
        if (Mode == BoardControllerMode.Unknown) Observe(750);
        if (Mode != BoardControllerMode.Console) return null;
        string marker = "JZ2440_CTL_LIST_" + DateTime.UtcNow.Ticks.ToString();
        if (!SyncConsole(marker, timeoutMilliseconds)) return null;
        serial.Write("/opt/jz2440/bin/appctl list\n");
        serial.Write("echo " + marker + "\n");
        List<string> output = new List<string>();
        DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMilliseconds);
        while (DateTime.UtcNow < deadline)
        {
            IList<string> lines = ReadLines(serial.ReadAvailable());
            bool found = false;
            foreach (string line in lines)
            {
                if (line.IndexOf(marker, StringComparison.Ordinal) >= 0)
                {
                    found = true;
                    break;
                }
                if (line.Trim().Length != 0) output.Add(line.TrimEnd('\r', '\n'));
            }
            if (found) return output;
            Thread.Sleep(25);
        }
        return null;
    }

    public string QueryStatus(int timeoutMilliseconds)
    {
        if (Mode == BoardControllerMode.Unknown) Observe(750);
        if (Mode != BoardControllerMode.Console) return null;
        string marker = "JZ2440_CTL_STATUS_" + DateTime.UtcNow.Ticks.ToString();
        if (!SyncConsole(marker, timeoutMilliseconds)) return null;
        serial.Write("/opt/jz2440/bin/appctl status\n");
        serial.Write("echo " + marker + "\n");
        DateTime deadline = DateTime.UtcNow.AddMilliseconds(timeoutMilliseconds);
        while (DateTime.UtcNow < deadline)
        {
            IList<string> lines = ReadLines(serial.ReadAvailable());
            foreach (string line in lines)
            {
                if (line.IndexOf(marker, StringComparison.Ordinal) >= 0) return null;
                string value = line.Trim();
                if (value == "QTOPIA" || value == "STOPPED" || value.StartsWith("APP ", StringComparison.Ordinal))
                    return value;
            }
            Thread.Sleep(25);
        }
        return null;
    }

    private IList<string> ReadLines(string incoming)
    {
        List<string> lines = new List<string>();
        if (!string.IsNullOrEmpty(incoming)) pending.Append(incoming);
        while (true)
        {
            int newline = pending.ToString().IndexOf('\n');
            if (newline < 0) break;
            lines.Add(pending.ToString(0, newline + 1));
            pending.Remove(0, newline + 1);
        }
        if (pending.Length > 1024) pending.Remove(0, pending.Length - 255);
        return lines;
    }
}
