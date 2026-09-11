using System;
using System.Collections.Generic;

public static class RunBoardLifecycleTests
{
    private sealed class FakeTransport : ISerialTransport
    {
        private readonly Queue<string> incoming = new Queue<string>();
        public readonly List<string> Writes = new List<string>();
        public string StopLine;
        public string ImmediateStopLine;
        public string PortName { get { return "FAKE"; } }
        public void Add(string text) { incoming.Enqueue(text); }
        public string ReadAvailable() { return incoming.Count == 0 ? "" : incoming.Dequeue(); }
        public void Write(string frame)
        {
            Writes.Add(frame);
            if (frame.StartsWith("echo ", StringComparison.Ordinal))
                Add(frame.Substring(5).TrimEnd('\n') + "\n");
            else if (frame == "/opt/jz2440/bin/appctl start runboard\n")
                Add(ImmediateStopLine == null ? "<APPREADY|runboard>\n" :
                    "<APPREADY|runboard>\n" + ImmediateStopLine + "\n");
            else if (frame == "<RBQUIT>\n" && StopLine != null)
                Add(StopLine + "\n");
        }
        public void Dispose() { }
    }

    private static BoardController Start(FakeTransport fake)
    {
        BoardController controller = new BoardController(fake, new RunBoardAdapter());
        controller.ConfirmConsoleMode();
        if (!controller.StartApplication(250) || controller.Mode != BoardControllerMode.Application)
            throw new InvalidOperationException("RunBoard start simulation failed");
        return controller;
    }

    private static void Check(bool value, string message)
    {
        if (!value) throw new InvalidOperationException(message);
    }

    public static void Run()
    {
        FakeTransport fake = new FakeTransport { StopLine = "<APPSTOP|runboard|RC=0>" };
        using (fake)
        {
            BoardController controller = Start(fake);
            Check(controller.StopApplication(250), "normal RBQUIT did not produce APPSTOP");
            Check(controller.Mode == BoardControllerMode.Console, "normal stop did not return to console");
            Check(controller.LastApplicationStopLine == "<APPSTOP|runboard|RC=0>\n", "normal stop line was not retained");
            int writes = fake.Writes.Count;
            Check(controller.StopApplication(250), "repeated stop was not idempotent");
            Check(fake.Writes.Count == writes, "repeated stop sent another wire command");
        }

        fake = new FakeTransport();
        using (fake)
        {
            BoardController controller = Start(fake);
            DateTime started = DateTime.UtcNow;
            Check(!controller.StopApplication(100), "missing APPSTOP was reported as success");
            Check((DateTime.UtcNow - started).TotalSeconds < 2, "stop timeout hung");
            Check(controller.Mode == BoardControllerMode.Application, "timeout lost fail-closed application mode");
        }

        fake = new FakeTransport { StopLine = "<APPSTOP|runboard|RC=7>" };
        using (fake)
        {
            BoardController controller = Start(fake);
            RunBoardAdapter adapter = new RunBoardAdapter();
            Check(controller.StopApplication(250), "abnormal APPSTOP was not observed");
            int code;
            Check(adapter.TryGetReturnCode(controller.LastApplicationStopLine, out code) && code == 7, "abnormal return code was not classified");
        }

        fake = new FakeTransport { ImmediateStopLine = "<APPSTOP|runboard|RC=1>" };
        using (fake)
        {
            BoardController controller = Start(fake);
            Check(controller.TakePendingApplicationData().IndexOf("APPSTOP|runboard|RC=1", StringComparison.Ordinal) >= 0,
                  "already-exited child marker was discarded");
            Check(!controller.StopApplication(100), "already-exited child without fresh stop marker was accepted");
        }

        Console.WriteLine("runboard lifecycle simulation: PASS");
    }
}
