using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Jz2440.Control.Core;

namespace Jz2440.Control.Core.Tests
{
    internal static class Program
    {
        private static async Task<int> Main(string[] args)
        {
            try
            {
                if (args.Length == 1 && args[0] == "--hardware")
                    return await HardwareSmoke();
                RegistryTransitions();
                ProtocolFrames();
                CodexSessionFraming();
                BufferedTransportLifecycle();
                ConsoleMarkerHandling();
                ApplicationAttachProbeTests();
                await MockConnectionScenarios();
                Console.WriteLine("JZ2440 Control core tests: PASS");
                return 0;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine("JZ2440 Control core tests: FAIL");
                Console.Error.WriteLine(error.Message);
                return 1;
            }
        }

        private static async Task<int> HardwareSmoke()
        {
            AppRegistry registry = AppRegistry.CreateDefault();
            AppConfiguration configuration = new AppConfiguration
            {
                OperationTimeoutMs = 8000,
                RunBoardFrameIntervalMs = 1000
            };
            RecordingLogger logger = new RecordingLogger();
            SerialDeviceConnection device = new SerialDeviceConnection(registry, logger, null, configuration);
            try
            {
                await device.ConnectAsync(null, CancellationToken.None);
                ReportHardware("connect", device.Snapshot);
                Assert(device.Snapshot.ConnectionState == DeviceConnectionState.Connected, "hardware connect failed");
                Assert(device.Snapshot.CurrentAppId == "qtopia", "hardware baseline is not Qtopia");
                Assert(device.Snapshot.Apps.Any(item => item.Id == "runboard" && item.State == AppState.Available),
                    "RunBoard was not verified by appctl list");

                await SwitchAndAssert(device, "codex-monitor");
                await Task.Delay(20000);
                Assert(logger.Infos.Contains("Codex provider response sent."), "Codex provider did not send a response");
                await SwitchAndAssert(device, "qtopia");
                await SwitchAndAssert(device, "runboard");
                Assert(logger.Infos.Contains("RunBoard provider first frame sent."), "RunBoard provider did not send a frame");
                await SwitchAndAssert(device, "qtopia");

                // Direct application-to-application transition through the
                // same Control owner and the same transport.
                await SwitchAndAssert(device, "codex-monitor");
                await SwitchAndAssert(device, "runboard");
                await SwitchAndAssert(device, "qtopia");

                await device.DisconnectAsync();
                await device.ConnectAsync(null, CancellationToken.None);
                ReportHardware("reconnect", device.Snapshot);
                Assert(device.Snapshot.ConnectionState == DeviceConnectionState.Connected &&
                       device.Snapshot.CurrentAppId == "qtopia", "logical reconnect did not restore Qtopia state");
                await device.DisconnectAsync();
                Console.WriteLine("JZ2440 Control hardware smoke: PASS");
                return 0;
            }
            catch (Exception error)
            {
                Console.Error.WriteLine("JZ2440 Control hardware smoke: FAIL " + error.GetType().Name + " " + error.Message);
                return 1;
            }
            finally
            {
                try
                {
                    if (device.Snapshot.ConnectionState == DeviceConnectionState.Connected &&
                        device.Snapshot.CurrentAppId != null && device.Snapshot.CurrentAppId != "qtopia")
                        await device.SwitchAsync("qtopia", CancellationToken.None);
                }
                catch { }
                try { await device.DisconnectAsync(); } catch { }
                device.Dispose();
            }
        }

        private static async Task SwitchAndAssert(SerialDeviceConnection device, string appId)
        {
            SwitchResult result = await device.SwitchAsync(appId, CancellationToken.None);
            ReportHardware("switch-" + appId, device.Snapshot);
            Assert(result.Success, "hardware switch failed: " + appId + " / " + result.ErrorCode);
            Assert(device.Snapshot.CurrentAppId == appId, "hardware current app mismatch: " + appId);
        }

        private static void ReportHardware(string step, DeviceSnapshot snapshot)
        {
            Console.WriteLine("HARDWARE step={0} connection={1} app={2}",
                step, snapshot.ConnectionState, snapshot.CurrentAppId ?? "unknown");
            if (step == "connect" && snapshot.CurrentAppId == null)
                Console.WriteLine("HARDWARE connect-message={0}", snapshot.Message);
        }

        private static void RegistryTransitions()
        {
            AppRegistry registry = AppRegistry.CreateDefault();
            Assert(registry.GetState("runboard") == AppState.Unavailable, "RunBoard starts unavailable");
            registry.SetCurrent("qtopia");
            Assert(registry.CurrentAppId == "qtopia", "Qtopia is current");
            registry.BeginStop("qtopia");
            Assert(registry.GetState("qtopia") == AppState.Stopping, "running app can stop");
            registry.CompleteStop("qtopia");
            registry.BeginStart("codex-monitor");
            Assert(registry.GetState("codex-monitor") == AppState.Starting, "available app can start");
            registry.CompleteStart("codex-monitor");
            Assert(registry.CurrentAppId == "codex-monitor" && registry.GetState("codex-monitor") == AppState.Running,
                "starting app becomes running");
            registry.Fail("codex-monitor", "test failure");
            AppCardSnapshot failed = registry.Snapshot().Single(item => item.Id == "codex-monitor");
            Assert(failed.State == AppState.Failed && failed.CanLaunch, "failed app is retryable");

            registry.SetAvailable("runboard", true);
            Assert(registry.GetState("runboard") == AppState.Available &&
                   registry.Snapshot().Single(item => item.Id == "runboard").CanLaunch,
                "RunBoard becomes launchable only after registry verification");

            registry.SetCurrent("qtopia");
            registry.Fail("runboard", "provider failure");
            Assert(registry.CurrentAppId == "qtopia" && registry.GetState("qtopia") == AppState.Running &&
                   registry.GetState("runboard") == AppState.Failed,
                "provider failure keeps the recovered Qtopia state and failed target visible");
        }

        private static void ProtocolFrames()
        {
            string encoded = AppControlProtocol.EncodeRequest(7, "START", "codex-monitor");
            Assert(AppControlProtocol.TryParse(encoded, out AppControlFrame parsed, out string error), error);
            Assert(parsed.Type == "REQ" && parsed.Get("ID") == "7" && parsed.Get("NAME") == "codex-monitor",
                "control request round trip");
            Assert(!AppControlProtocol.TryParse("<APP|REQ|ID=1|ID=2>\n", out parsed, out error),
                "duplicate fields are rejected");
            Assert(!AppControlProtocol.TryParse("<CQM1|5H=1>\n", out parsed, out error),
                "CQM payload is not a control frame");
        }

        private static void CodexSessionFraming()
        {
            RecordingTransport transport = new RecordingTransport();
            BoardApplicationSession session = new BoardApplicationSession(transport, delegate
            {
                return new QuotaSnapshot
                {
                    FiveHourRemaining = 80,
                    FiveHourReset = 100,
                    WeeklyRemaining = 70,
                    WeeklyReset = 200,
                    ResetCards = 0,
                    Now = 300,
                    TimeZoneOffsetMinutes = 480
                };
            });

            session.Consume("<CQMREQ|V=");
            Assert(transport.Writes.Count == 0, "partial CQM request does not respond early");
            session.Consume("1>\n<CQMREQ|V=1>\n");
            Assert(session.Responses == 2 && transport.Writes.Count == 2,
                "multiple CQM requests across partial framing are handled");
            Assert(ProtocolParser.TryParse(transport.Writes[0], out Dictionary<string, string> fields),
                "CQM1 response is valid");
            session.Consume("<CQMREQ|V=2>\n<malformed>\n");
            Assert(session.Responses == 2, "malformed and unsupported frames are ignored");
            session.Consume("<APPSTOP|codex-monitor|RC=0>\n");
            Assert(session.Stopped, "Codex lifecycle stop frame is recognized");
        }

        private static void BufferedTransportLifecycle()
        {
            RecordingTransport first = new RecordingTransport();
            BufferedSerialTransport buffered = new BufferedSerialTransport(first, delegate { });
            first.Enqueue("stale-partial");
            WaitUntil(delegate { return buffered.ReadAvailable() == "stale-partial"; }, "buffered input");
            buffered.Dispose();

            RecordingTransport second = new RecordingTransport();
            BufferedSerialTransport reconnected = new BufferedSerialTransport(second, delegate { });
            Assert(string.IsNullOrEmpty(reconnected.ReadAvailable()), "reconnect starts with a fresh buffer");
            reconnected.PushBack("stop\n");
            Assert(reconnected.ReadAvailable() == "stop\n", "push-back preserves lifecycle frame ordering");
            reconnected.Dispose();
        }

        private static void WaitUntil(Func<bool> condition, string name)
        {
            DateTime deadline = DateTime.UtcNow.AddSeconds(2);
            while (DateTime.UtcNow < deadline)
            {
                if (condition()) return;
                Thread.Sleep(10);
            }
            throw new InvalidOperationException(name + " did not arrive before timeout");
        }

        // The board console echoes the typed command line, so the marker also
        // appears inside the echo of "echo <marker>". Only the shell's own
        // output line proves that the console is actually usable.
        private static void ConsoleMarkerHandling()
        {
            EchoTransport echoOnly = new EchoTransport();
            BoardController echoController = new BoardController(echoOnly, new CodexMonitorAdapter());
            Assert(!echoController.SyncConsole("JZ2440_TEST_MARKER", 400),
                "echoed command line must not count as a console sync");
            Assert(echoOnly.Writes.Count == 1, "console sync writes exactly one probe command");

            EchoTransport withOutput = new EchoTransport { EmitCommandOutput = true };
            BoardController outputController = new BoardController(withOutput, new CodexMonitorAdapter());
            Assert(outputController.SyncConsole("JZ2440_TEST_MARKER", 2000),
                "shell output line confirms the console");
        }

        private static void ApplicationAttachProbeTests()
        {
            AttachProbeTransport runboard = new AttachProbeTransport("runboard");
            ApplicationAttachProbeResult detectedRunBoard = ApplicationAttachProbe.TryDetect(
                runboard, new AppConfiguration { OperationTimeoutMs = 100 }, new NullLogger());
            Assert(detectedRunBoard != null && detectedRunBoard.AppId == "runboard",
                "RunBoard attach probe did not recognize RBDBG");

            AttachProbeTransport codex = new AttachProbeTransport("codex-monitor");
            ApplicationAttachProbeResult detectedCodex = ApplicationAttachProbe.TryDetect(
                codex, new AppConfiguration { OperationTimeoutMs = 100 }, new NullLogger());
            Assert(detectedCodex != null && detectedCodex.AppId == "codex-monitor" &&
                   detectedCodex.InitialData == "<CQMREQ|V=1>\n",
                "Codex attach probe did not preserve the request frame");
        }

        private sealed class EchoTransport : ISerialTransport
        {
            private readonly Queue<string> incoming = new Queue<string>();
            public readonly List<string> Writes = new List<string>();
            public bool EmitCommandOutput { get; set; }
            public string PortName { get { return "echo"; } }

            public void Write(string frame)
            {
                Writes.Add(frame);
                string command = (frame ?? string.Empty).TrimEnd('\r', '\n');
                incoming.Enqueue(command + "\r\n");
                if (EmitCommandOutput && command.StartsWith("echo ", StringComparison.Ordinal))
                    incoming.Enqueue(command.Substring("echo ".Length) + "\r\n");
            }

            public string ReadAvailable()
            {
                return incoming.Count == 0 ? string.Empty : incoming.Dequeue();
            }

            public void Dispose() { }
        }

        private static async Task MockConnectionScenarios()
        {
            AppRegistry registry = AppRegistry.CreateDefault();
            MockDeviceConnection mock = new MockDeviceConnection(registry, new NullLogger());
            ControlViewModel viewModel = new ControlViewModel(mock, registry, new NullLogger());
            try
            {
                List<DeviceConnectionState> observedStates = new List<DeviceConnectionState>();
                mock.SnapshotChanged += delegate(object sender, DeviceSnapshotEventArgs args)
                {
                    observedStates.Add(args.Snapshot.ConnectionState);
                };
                Assert(viewModel.Snapshot.ConnectionState == DeviceConnectionState.Disconnected, "mock starts disconnected");
                await viewModel.ConnectAsync(null, CancellationToken.None);
                Assert(viewModel.Snapshot.ConnectionState == DeviceConnectionState.Connected &&
                       viewModel.Snapshot.CurrentAppId == "qtopia", "mock connects with Qtopia");
                Assert(observedStates.Contains(DeviceConnectionState.Connecting) &&
                       observedStates.Contains(DeviceConnectionState.Connected), "connection state transitions are emitted");
                Assert(viewModel.Apps.Single(item => item.Id == "runboard").State == AppState.Unavailable,
                    "RunBoard remains unavailable in mock");

                SwitchResult success = await viewModel.LaunchAsync("codex-monitor", CancellationToken.None);
                Assert(success.Success && viewModel.Snapshot.CurrentAppId == "codex-monitor", "mock switch succeeds");

                mock.FailNextSwitchForDevelopment();
                SwitchResult failure = await viewModel.LaunchAsync("qtopia", CancellationToken.None);
                Assert(!failure.Success, "mock can report switch failure");
                Assert(viewModel.Apps.Single(item => item.Id == "qtopia").State == AppState.Failed,
                    "failed switch exposes Failed state");
                SwitchResult recovered = await viewModel.LaunchAsync("qtopia", CancellationToken.None);
                Assert(recovered.Success && viewModel.Snapshot.CurrentAppId == "qtopia",
                    "failed switch can be retried");

                Task<SwitchResult> first = viewModel.LaunchAsync("codex-monitor", CancellationToken.None);
                Task<SwitchResult> second = viewModel.LaunchAsync("codex-monitor", CancellationToken.None);
                SwitchResult[] duplicateResults = await Task.WhenAll(first, second);
                Assert(duplicateResults.Any(result => result.ErrorCode == "SWITCHING"), "duplicate launch is blocked");

                mock.SetCurrentForDevelopment("runboard");
                Assert(viewModel.Snapshot.CurrentAppId == "runboard" &&
                       viewModel.Apps.Single(item => item.Id == "runboard").State == AppState.Running,
                    "mock can represent a running RunBoard");
                mock.DisconnectForDevelopment();
                Assert(viewModel.Snapshot.ConnectionState == DeviceConnectionState.Disconnected &&
                       viewModel.Snapshot.CurrentAppId == null, "mock serial disconnect is represented");
            }
            finally
            {
                viewModel.Dispose();
            }
        }

        private static void Assert(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }

        private sealed class RecordingLogger : ILogger
        {
            public readonly List<string> Infos = new List<string>();
            public readonly List<string> Errors = new List<string>();
            public void Info(string message) { Infos.Add(message); }
            public void Error(string message, Exception error = null) { Errors.Add(message); }
        }

        private sealed class RecordingTransport : ISerialTransport
        {
            private readonly object gate = new object();
            private readonly Queue<string> incoming = new Queue<string>();
            public readonly List<string> Writes = new List<string>();
            public string PortName { get { return "test"; } }

            public void Enqueue(string value)
            {
                lock (gate) incoming.Enqueue(value);
            }

            public void Write(string frame)
            {
                lock (gate) Writes.Add(frame);
            }

            public string ReadAvailable()
            {
                lock (gate) return incoming.Count == 0 ? string.Empty : incoming.Dequeue();
            }

            public void Dispose() { }
        }

        private sealed class AttachProbeTransport : ISerialTransport
        {
            private readonly Queue<string> incoming = new Queue<string>();
            private readonly string application;
            public string PortName { get { return "probe"; } }

            public AttachProbeTransport(string applicationId)
            {
                application = applicationId;
            }

            public void Write(string frame)
            {
                if (frame.StartsWith("RB1|", StringComparison.Ordinal))
                {
                    if (application == "runboard") incoming.Enqueue("RBDBG|phase=PARSED|parse=FAIL|\n");
                    else incoming.Enqueue("<CQMREQ|V=1>\n");
                }
            }

            public string ReadAvailable()
            {
                return incoming.Count == 0 ? string.Empty : incoming.Dequeue();
            }

            public void Dispose() { }
        }
    }
}
