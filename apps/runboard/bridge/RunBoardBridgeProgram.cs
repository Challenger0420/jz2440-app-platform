using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;

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

public static class RunBoardBridgeProgram
{
    private static readonly RunBoardAdapter Adapter = new RunBoardAdapter();

    public static int Main(string[] args)
    {
        try
        {
            if (args.Length == 1 && args[0] == "--self-test") { SelfTest(); return 0; }
            if (args.Length > 0 && args[0] == "board") return BoardCommand(args);
            PrintUsage();
            return 2;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine("runboard bridge failed: {0}: {1}", error.GetType().Name, error.Message);
            return 1;
        }
    }

    private static int BoardCommand(string[] args)
    {
        string command = args.Length > 1 ? args[1] : "";
        string port = null;
        bool console = false;
        bool application = false;
        bool trace = false;
        bool live = false;
        string scenario = "idle";
        int interval = 10;
        int duration = 0;
        string python = "python";
        for (int i = 2; i < args.Length; i++)
        {
            if (args[i] == "--port" && i + 1 < args.Length) port = args[++i];
            else if (args[i] == "--console") console = true;
            else if (args[i] == "--application") application = true;
            else if (args[i] == "--serial-trace") trace = true;
            else if (args[i] == "--live") live = true;
            else if (args[i] == "--scenario" && i + 1 < args.Length) scenario = args[++i];
            else if (args[i] == "--interval" && i + 1 < args.Length) interval = ParsePositive(args[++i], "interval");
            else if (args[i] == "--duration" && i + 1 < args.Length) duration = ParseNonNegative(args[++i], "duration");
            else if (args[i] == "--python" && i + 1 < args.Length) python = args[++i];
            else throw new ArgumentException("unknown board argument: " + args[i]);
        }
        if (command == "list") return List(port, console, trace);
        if (command == "status") return Status(port, trace);
        if (command == "stop") return Stop(port, application, trace);
        if (command == "start") return Start(port, console, trace, live, scenario, interval, duration, python);
        PrintUsage();
        return 2;
    }

    private static int List(string port, bool console, bool trace)
    {
        using (ISerialTransport opened = SerialTransport.Open(FindPort(port)))
        {
            BoardController controller;
            ISerialTransport serial = Wrap(opened, trace, out controller);
            using (serial == opened ? null : serial)
            {
                if (console) controller.ConfirmConsoleMode();
                IList<string> apps = controller.ListApplications(5000);
                if (apps == null) throw new InvalidOperationException("board is not in console mode or appctl list timed out");
                foreach (string app in apps) Console.WriteLine(app);
            }
        }
        return 0;
    }

    private static int Status(string port, bool trace)
    {
        using (ISerialTransport opened = SerialTransport.Open(FindPort(port)))
        {
            BoardController controller;
            ISerialTransport serial = Wrap(opened, trace, out controller);
            using (serial == opened ? null : serial)
            {
                BoardControllerMode mode = controller.Observe(1500);
                Console.WriteLine("port={0} mode={1}", opened.PortName, mode);
            }
        }
        return 0;
    }

    private static int Stop(string port, bool application, bool trace)
    {
        using (ISerialTransport opened = SerialTransport.Open(FindPort(port)))
        {
            BoardController controller;
            ISerialTransport serial = Wrap(opened, trace, out controller);
            using (serial == opened ? null : serial)
            {
                if (application) controller.ConfirmApplicationMode();
                if (!controller.StopApplication(8000))
                {
                    Console.Error.WriteLine("RUNBOARD_STOP=UNCONFIRMED");
                    throw new InvalidOperationException("runboard APPSTOP not observed before timeout");
                }
                int returnCode;
                if (!Adapter.TryGetReturnCode(controller.LastApplicationStopLine, out returnCode))
                    throw new InvalidOperationException("runboard APPSTOP had no valid return code");
                if (returnCode != 0)
                {
                    Console.Error.WriteLine("RUNBOARD_STOP=ABNORMAL RC={0}", returnCode);
                    throw new InvalidOperationException("runboard exited abnormally");
                }
                Console.WriteLine("APPSTOP observed; appctl restored Qtopia; RC=0");
            }
        }
        return 0;
    }

    private static int Start(string port, bool console, bool trace, bool live, string scenario,
                             int interval, int duration, string python)
    {
        ValidateScenario(scenario);
        DateTime deadline = duration == 0 ? DateTime.MaxValue : DateTime.UtcNow.AddSeconds(duration);
        using (ISerialTransport opened = SerialTransport.Open(FindPort(port)))
        {
            BoardController controller;
            ISerialTransport serial = Wrap(opened, trace, out controller);
            using (serial == opened ? null : serial)
            {
                if (console) controller.ConfirmConsoleMode();
                if (!controller.StartApplication(15000))
                    throw new InvalidOperationException("runboard APPREADY or RB1 traffic not observed");
                Console.WriteLine("APPREADY observed; streaming RB1 ({0})", live ? "live" : scenario);
                string pending = controller.TakePendingApplicationData();
                if (!String.IsNullOrEmpty(pending)) Console.WriteLine("initial application data={0}", pending.Trim());
                int sequence = 0;
                while (DateTime.UtcNow < deadline)
                {
                    string frame = ReadFrame(python, live, scenario, sequence++);
                    serial.Write(frame);
                    Console.WriteLine("RB1 sent seq={0} bytes={1}", sequence - 1, Encoding.ASCII.GetByteCount(frame));
                    Thread.Sleep(interval * 1000);
                    string incoming = serial.ReadAvailable();
                    if (incoming.IndexOf("<APPSTOP|runboard|", StringComparison.Ordinal) >= 0) break;
                }
            }
        }
        return 0;
    }

    private static string ReadFrame(string python, bool live, string scenario, int sequence)
    {
        string script = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..\\..\\apps\\runboard\\host\\runboard_state_cli.py");
        script = Path.GetFullPath(script);
        List<string> arguments = new List<string> { "-u", script, live ? "--live" : "--scenario", live ? "" : scenario, "--sequence", sequence.ToString() };
        if (live) arguments.RemoveAt(3);
        ProcessStartInfo info = new ProcessStartInfo
        {
            FileName = python,
            WorkingDirectory = FindRoot(script),
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true
        };
        StringBuilder commandLine = new StringBuilder();
        foreach (string argument in arguments)
        {
            if (argument.Length == 0) continue;
            if (commandLine.Length != 0) commandLine.Append(' ');
            commandLine.Append('"').Append(argument.Replace("\\", "\\\\").Replace("\"", "\\\""));
            commandLine.Append('"');
        }
        info.Arguments = commandLine.ToString();
        using (Process process = Process.Start(info))
        {
            if (!process.WaitForExit(30000)) { try { process.Kill(); } catch { } throw new TimeoutException("state provider timed out"); }
            string output = process.StandardOutput.ReadToEnd();
            string error = process.StandardError.ReadToEnd();
            if (process.ExitCode != 0) throw new InvalidOperationException(error.Trim());
            if (String.IsNullOrEmpty(output)) throw new InvalidOperationException("state provider returned no RB1 frame");
            return output;
        }
    }

    private static string FindRoot(string script)
    {
        DirectoryInfo directory = new DirectoryInfo(Path.GetDirectoryName(script));
        while (directory != null && !File.Exists(Path.Combine(directory.FullName, "config", "config.example.json"))) directory = directory.Parent;
        return directory == null ? Directory.GetCurrentDirectory() : directory.FullName;
    }

    private static ISerialTransport Wrap(ISerialTransport opened, bool trace, out BoardController controller)
    {
        BoardController[] holder = new BoardController[1];
        if (!trace) { controller = new BoardController(opened, Adapter); return opened; }
        ISerialTransport traced = new SerialTraceTransport(opened, delegate { return holder[0] == null ? "Unknown" : holder[0].Mode.ToString(); });
        holder[0] = new BoardController(traced, Adapter);
        controller = holder[0];
        return traced;
    }

    private static string FindPort(string overridePort)
    {
        if (!String.IsNullOrWhiteSpace(overridePort)) return overridePort;
        IList<ComPortCandidate> candidates = new ProlificComPortDiscovery().Find();
        if (candidates.Count != 1) throw new InvalidOperationException("expected exactly one compatible PL2303 COM device; found " + candidates.Count);
        return candidates[0].PortName;
    }

    private static void ValidateScenario(string scenario)
    {
        if (scenario != "idle" && scenario != "single" && scenario != "double" && scenario != "completed" && scenario != "error")
            throw new ArgumentException("invalid scenario: " + scenario);
    }
    private static int ParsePositive(string value, string name) { int parsed = ParseNonNegative(value, name); if (parsed <= 0) throw new ArgumentException(name + " must be positive"); return parsed; }
    private static int ParseNonNegative(string value, string name) { int parsed; if (!Int32.TryParse(value, out parsed) || parsed < 0) throw new ArgumentException(name + " must be non-negative"); return parsed; }
    private static void SelfTest()
    {
        RunBoardAdapter adapter = new RunBoardAdapter();
        int returnCode;
        if (!adapter.IsApplicationTraffic("RB1|L=1|V=1|CRC=0000\n") || !adapter.IsReady("<APPREADY|runboard>\n") || !adapter.IsStopped("<APPSTOP|runboard|RC=0>\n") || !adapter.TryGetReturnCode("<APPSTOP|runboard|RC=7>\n", out returnCode) || returnCode != 7 || adapter.IsStopped("<APPSTOP|runboard|RC=x>\n")) throw new Exception("adapter self-test failed");
        RunBoardLifecycleTests.Run();
        Console.WriteLine("RUNBOARD_BRIDGE_SELF_TEST=PASS");
    }
    private static void PrintUsage()
    {
        Console.WriteLine("RunBoard host bridge");
        Console.WriteLine("  board status [--port COMx]");
        Console.WriteLine("  board list [--console] [--port COMx]");
        Console.WriteLine("  board start [--console] [--live|--scenario idle|single|double|completed|error] [--interval N] [--duration N]");
        Console.WriteLine("  board stop --application [--port COMx]");
        Console.WriteLine("  --serial-trace enables raw host serial diagnostics");
    }
}
