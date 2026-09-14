using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace Jz2440.Control.Core
{
    public sealed class SerialDeviceConnection : IDeviceConnection
    {
        private readonly AppRegistry registry;
        private readonly ILogger logger;
        private readonly global::IComPortDiscovery discovery;
        private readonly AppConfiguration configuration;
        private readonly SemaphoreSlim operationLock = new SemaphoreSlim(1, 1);
        private DeviceConnectionState connectionState = DeviceConnectionState.Disconnected;
        private BufferedSerialTransport transport;
        private global::BoardController boardController;
        private CodexApplicationProviderSession codexProvider;
        private RunBoardApplicationProviderSession runBoardProvider;
        private string portName;
        private string message = "Disconnected.";
        private bool disposed;

        public SerialDeviceConnection(AppRegistry appRegistry, ILogger appLogger,
                                      global::IComPortDiscovery portDiscovery = null,
                                      AppConfiguration appConfiguration = null)
        {
            registry = appRegistry ?? throw new ArgumentNullException("appRegistry");
            logger = appLogger ?? new NullLogger();
            discovery = portDiscovery ?? new global::ProlificComPortDiscovery();
            configuration = appConfiguration ?? new AppConfiguration();
        }

        public event EventHandler<DeviceSnapshotEventArgs> SnapshotChanged;
        public DeviceSnapshot Snapshot { get { return CreateSnapshot(); } }

        public async Task<IReadOnlyList<string>> DiscoverPortsAsync(CancellationToken cancellationToken)
        {
            ThrowIfDisposed();
            IList<global::ComPortCandidate> candidates = await Task.Run(() => discovery.Find(), cancellationToken)
                .ConfigureAwait(false);
            return candidates.Select(candidate => candidate.PortName).ToList();
        }

        public async Task ConnectAsync(string requestedPortName, CancellationToken cancellationToken)
        {
            ThrowIfDisposed();
            await operationLock.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                StopProviderSessions();
                await CloseTransportAsync().ConfigureAwait(false);
                connectionState = DeviceConnectionState.Connecting;
                message = "Connecting...";
                registry.SetUnknown();
                Publish();

                string selectedPort = requestedPortName;
                if (string.IsNullOrWhiteSpace(selectedPort))
                {
                    IList<global::ComPortCandidate> candidates = await Task.Run(() => discovery.Find(), cancellationToken)
                        .ConfigureAwait(false);
                    if (candidates.Count != 1)
                    {
                        connectionState = DeviceConnectionState.Error;
                        message = candidates.Count == 0 ? "No compatible serial device found." : "Select one serial device.";
                        Publish();
                        return;
                    }
                    selectedPort = candidates[0].PortName;
                }

                cancellationToken.ThrowIfCancellationRequested();
                global::ISerialTransport opened = global::SerialTransport.Open(selectedPort);
                BufferedSerialTransport buffered = null;
                buffered = new BufferedSerialTransport(opened, error => OnTransportFault(buffered, error));
                transport = buffered;
                portName = selectedPort;

                CompositeApplicationAdapter composite = new CompositeApplicationAdapter();
                global::BoardController probeController = new global::BoardController(buffered, composite);
                boardController = probeController;
                global::BoardControllerMode mode = await Task.Run(() => Probe(probeController), cancellationToken)
                    .ConfigureAwait(false);
                if (buffered.IsFaulted) throw new InvalidOperationException("Serial device disconnected during probe.");

                if (mode == global::BoardControllerMode.Application && !string.IsNullOrEmpty(composite.DetectedAppId))
                {
                    string detected = composite.DetectedAppId;
                    ApplyApplicationAvailability(null, detected);
                    registry.SetCurrent(detected);
                    connectionState = DeviceConnectionState.Connected;
                    message = "Connected · running " + detected + ".";
                    Publish();
                    StartProviderForCurrent(detected, probeController.TakePendingApplicationData());
                    logger.Info("Serial connection established in application mode: " + detected + ".");
                    return;
                }

                if (mode != global::BoardControllerMode.Console)
                    throw new InvalidOperationException("Board probe did not confirm console or application mode.");

                IList<string> installedApps = await Task.Run(() => probeController.ListApplications(3000), cancellationToken)
                    .ConfigureAwait(false);
                ApplyApplicationAvailability(installedApps, null);
                string boardStatus = await Task.Run(() => QueryBoardStatus(probeController), cancellationToken)
                    .ConfigureAwait(false);
                if (string.Equals(boardStatus, "QTOPIA", StringComparison.Ordinal))
                    registry.SetCurrent("qtopia");
                else
                    registry.SetUnknown();
                connectionState = DeviceConnectionState.Connected;
                message = string.Equals(boardStatus, "QTOPIA", StringComparison.Ordinal)
                    ? (installedApps == null ? "Connected · Qtopia; application list unavailable." : "Connected · Qtopia.")
                    : "Connected · current app unknown (status=" + (boardStatus ?? "none") + ").";
                logger.Info("Serial connection established in console mode.");
                Publish();
            }
            catch (OperationCanceledException)
            {
                StopProviderSessions();
                await CloseTransportAsync().ConfigureAwait(false);
                connectionState = DeviceConnectionState.Disconnected;
                message = "Connection cancelled.";
                Publish();
                throw;
            }
            catch (Exception error)
            {
                StopProviderSessions();
                await CloseTransportAsync().ConfigureAwait(false);
                connectionState = DeviceConnectionState.Error;
                registry.SetUnknown();
                message = "Connection failed: " + error.Message;
                logger.Error("Serial connection failed.", error);
                Publish();
            }
            finally
            {
                operationLock.Release();
            }
        }

        public async Task DisconnectAsync()
        {
            ThrowIfDisposed();
            await operationLock.WaitAsync().ConfigureAwait(false);
            try
            {
                StopProviderSessions();
                await CloseTransportAsync().ConfigureAwait(false);
                connectionState = DeviceConnectionState.Disconnected;
                registry.SetUnknown();
                message = "Disconnected.";
                Publish();
            }
            finally
            {
                operationLock.Release();
            }
        }

        public async Task<SwitchResult> SwitchAsync(string appId, CancellationToken cancellationToken)
        {
            ThrowIfDisposed();
            if (connectionState != DeviceConnectionState.Connected || transport == null || transport.IsFaulted)
                return Failure(appId, "NOT_CONNECTED", "Device is not connected.");

            await operationLock.WaitAsync(cancellationToken).ConfigureAwait(false);
            try
            {
                AppCardSnapshot target = registry.Snapshot().FirstOrDefault(item => item.Id == appId);
                if (target == null || !target.CanLaunch)
                    return Failure(appId, "UNAVAILABLE", "Application is unavailable.");
                if (registry.CurrentAppId == appId)
                    return Success(appId);

                StopProviderSessions();
                string oldAppId = registry.CurrentAppId;
                if (string.IsNullOrEmpty(oldAppId))
                    return Failure(appId, "CURRENT_UNKNOWN", "Current board application is unknown; reconnect first.");

                if (oldAppId != "qtopia")
                {
                    registry.BeginStop(oldAppId);
                    message = "Stopping " + oldAppId + "...";
                    Publish();
                    global::BoardController stopController = NewController(oldAppId);
                    stopController.ConfirmApplicationMode();
                    if (!await Task.Run(() => StopApplicationConfirmed(stopController), cancellationToken)
                        .ConfigureAwait(false))
                    {
                        registry.Fail(oldAppId, "Stop was not confirmed.");
                        message = "Failed to stop " + oldAppId + ".";
                        Publish();
                        return Failure(appId, "STOP_FAILED", message);
                    }
                    registry.CompleteStop(oldAppId);
                    boardController = stopController;
                }
                else
                {
                    registry.BeginStop("qtopia");
                    message = "Stopping Qtopia...";
                    Publish();
                    registry.CompleteStop("qtopia");
                }

                if (appId == "qtopia")
                {
                    registry.SetCurrent("qtopia");
                    message = "Running Qtopia.";
                    Publish();
                    return Success(appId);
                }

                registry.BeginStart(appId);
                message = "Starting " + appId + "...";
                Publish();
                global::BoardController startController = NewController(appId);
                startController.ConfirmConsoleMode();
                if (!await Task.Run(() => startController.StartApplication(configuration.OperationTimeoutMs), cancellationToken)
                    .ConfigureAwait(false))
                {
                    registry.Fail(appId, "APPREADY was not confirmed.");
                    message = "Failed to start " + appId + ".";
                    Publish();
                    return Failure(appId, "START_FAILED", message);
                }

                string pending = startController.TakePendingApplicationData();
                try
                {
                    StartProviderForTarget(appId, pending);
                }
                catch (Exception providerError)
                {
                    logger.Error("Application provider failed to start.", providerError);
                    bool stopped = await Task.Run(() => StopApplicationConfirmed(startController), cancellationToken)
                        .ConfigureAwait(false);
                    if (stopped)
                    {
                        registry.SetCurrent("qtopia");
                        // Keep the failed target visible as retryable after
                        // the board has been safely recovered to Qtopia.
                        registry.Fail(appId, providerError.Message);
                        message = "Provider unavailable; recovered to Qtopia.";
                    }
                    else
                    {
                        connectionState = DeviceConnectionState.Error;
                        registry.SetUnknown();
                        message = "Provider unavailable and stop was not confirmed.";
                    }
                    Publish();
                    return Failure(appId, "PROVIDER_FAILED", message);
                }

                boardController = startController;
                registry.CompleteStart(appId);
                message = "Running " + appId + ".";
                Publish();
                return Success(appId);
            }
            catch (OperationCanceledException)
            {
                // A cancelled switch may have stopped or started an app
                // without receiving its lifecycle confirmation. Do not leave
                // a stale Running/Starting/Stopping state visible.
                connectionState = DeviceConnectionState.Error;
                registry.SetUnknown();
                message = "Application switch cancelled.";
                Publish();
                throw;
            }
            catch (Exception error)
            {
                logger.Error("Serial application switch failed.", error);
                registry.Fail(appId, error.Message);
                message = "Application switch failed.";
                Publish();
                return Failure(appId, "SWITCH_FAILED", message);
            }
            finally
            {
                operationLock.Release();
            }
        }

        public void Dispose()
        {
            if (disposed) return;
            disposed = true;
            StopProviderSessions();
            CloseTransportAsync().GetAwaiter().GetResult();
            operationLock.Dispose();
        }

        private global::BoardControllerMode Probe(global::BoardController controller)
        {
            global::BoardControllerMode mode = controller.Observe(1200);
            if (mode == global::BoardControllerMode.Application) return mode;
            string marker = "JZ2440_CONTROL_PROBE_" + DateTime.UtcNow.Ticks.ToString();
            return controller.SyncConsole(marker, configuration.OperationTimeoutMs)
                ? global::BoardControllerMode.Console
                : global::BoardControllerMode.Unknown;
        }

        private string QueryBoardStatus(global::BoardController controller)
        {
            string lastStatus = null;
            for (int attempt = 0; attempt < 5; attempt++)
            {
                lastStatus = controller.QueryStatus(2000);
                if (!string.IsNullOrEmpty(lastStatus) && lastStatus != "STOPPED") return lastStatus;
                Thread.Sleep(500);
            }
            return lastStatus;
        }

        private bool StopApplicationConfirmed(global::BoardController controller)
        {
            if (controller.StopApplication(configuration.OperationTimeoutMs)) return true;

            // A provider may have consumed the APPSTOP line just as a target
            // exited. Accept the stop only when a fresh console echo proves
            // that the application no longer owns the UART.
            string marker = "JZ2440_CONTROL_STOP_" + DateTime.UtcNow.Ticks.ToString();
            if (controller.SyncConsole(marker, configuration.OperationTimeoutMs))
            {
                logger.Info("Application stop recovered by console probe.");
                return true;
            }
            return false;
        }

        private global::BoardController NewController(string appId)
        {
            global::IApplicationAdapter adapter;
            if (appId == "codex-monitor") adapter = new global::CodexMonitorAdapter();
            else if (appId == "runboard") adapter = new global::RunBoardAdapter();
            else throw new InvalidOperationException("No board adapter for " + appId + ".");
            return new global::BoardController(transport, adapter);
        }

        private void StartProviderForCurrent(string appId, string initialData)
        {
            if (appId == "codex-monitor")
            {
                codexProvider = new CodexApplicationProviderSession(transport, logger,
                    error => OnProviderFault(appId, error));
                codexProvider.Start(initialData);
            }
            else if (appId == "runboard")
            {
                runBoardProvider = new RunBoardApplicationProviderSession(transport, configuration, logger,
                    error => OnProviderFault(appId, error));
                runBoardProvider.Start();
            }
        }

        private void StartProviderForTarget(string appId, string initialData)
        {
            StartProviderForCurrent(appId, initialData);
            if (appId != "codex-monitor" && appId != "runboard")
                throw new InvalidOperationException("No application provider for " + appId + ".");
        }

        private void StopProviderSessions()
        {
            if (codexProvider != null) { codexProvider.Dispose(); codexProvider = null; }
            if (runBoardProvider != null) { runBoardProvider.Dispose(); runBoardProvider = null; }
        }

        private void ApplyApplicationAvailability(IList<string> installedApps, string currentAppId)
        {
            bool haveList = installedApps != null;
            bool codex = currentAppId == "codex-monitor" || (haveList && ContainsApp(installedApps, "codex-monitor"));
            bool runboard = currentAppId == "runboard" || (haveList && ContainsApp(installedApps, "runboard"));
            registry.SetAvailable("codex-monitor", codex, codex ? null : "Not registered by appctl.");
            registry.SetAvailable("runboard", runboard, runboard ? null : "Not verified by appctl.");
            registry.SetAvailable("qtopia", true);
        }

        private static bool ContainsApp(IList<string> values, string appId)
        {
            return values.Any(value => string.Equals(value == null ? null : value.Trim(), appId, StringComparison.Ordinal));
        }

        private void OnProviderFault(string appId, Exception error)
        {
            if (disposed) return;
            logger.Error("Application provider fault.", error);
            if (registry.CurrentAppId == appId)
            {
                // The provider owns the active application's data path. Once
                // it exits unexpectedly, the host must not leave that app
                // looking Running or permit a blind switch from it.
                connectionState = DeviceConnectionState.Error;
                registry.Fail(appId, error == null ? "Provider stopped unexpectedly." : error.Message);
                message = "Application provider failed; reconnect required.";
            }
            else
            {
                message = "Application provider stopped.";
            }
            Publish();
        }

        private void OnTransportFault(BufferedSerialTransport source, Exception error)
        {
            if (disposed || !ReferenceEquals(source, transport)) return;
            // A reader fault after a successful connection means the physical
            // device disappeared. Reserve Error for probe/protocol failures;
            // the UI reconnect scheduler watches Disconnected.
            connectionState = DeviceConnectionState.Disconnected;
            registry.SetUnknown();
            message = "Serial connection lost.";
            logger.Error("Serial connection lost.", error);
            Publish();

            // The callback is raised by BufferedSerialTransport's reader
            // thread.  Cleanup must therefore be serialized with connect and
            // switch operations, but cannot dispose the reader synchronously
            // from inside itself.  Reference-checking prevents a stale fault
            // from closing a newer transport opened during reconnect.
            _ = CleanupFaultedTransportAsync(source);
        }

        private async Task CleanupFaultedTransportAsync(BufferedSerialTransport source)
        {
            try
            {
                await operationLock.WaitAsync().ConfigureAwait(false);
                try
                {
                    if (disposed || !ReferenceEquals(source, transport)) return;
                    StopProviderSessions();
                    await CloseTransportAsync().ConfigureAwait(false);
                }
                finally
                {
                    operationLock.Release();
                }
            }
            catch (Exception cleanupError)
            {
                // The connection is already fail-closed.  Keep cleanup
                // failures local and let the next explicit/automatic connect
                // attempt establish a fresh transport.
                logger.Error("Serial fault cleanup failed.", cleanupError);
            }
        }

        private async Task CloseTransportAsync()
        {
            BufferedSerialTransport oldTransport = transport;
            transport = null;
            boardController = null;
            if (oldTransport != null) await Task.Run(() => oldTransport.Dispose()).ConfigureAwait(false);
        }

        private DeviceSnapshot CreateSnapshot()
        {
            return new DeviceSnapshot
            {
                ConnectionState = connectionState,
                PortName = portName,
                CurrentAppId = registry.CurrentAppId,
                Message = message,
                IsDevelopmentMock = false,
                Apps = registry.Snapshot()
            };
        }

        private void Publish()
        {
            SnapshotChanged?.Invoke(this, new DeviceSnapshotEventArgs(CreateSnapshot()));
        }

        private static SwitchResult Success(string appId)
        {
            return new SwitchResult { Success = true, AppId = appId, Message = "Switch completed." };
        }

        private static SwitchResult Failure(string appId, string code, string text)
        {
            return new SwitchResult { Success = false, AppId = appId, ErrorCode = code, Message = text };
        }

        private void ThrowIfDisposed()
        {
            if (disposed) throw new ObjectDisposedException(GetType().Name);
        }

        private sealed class CompositeApplicationAdapter : global::IApplicationAdapter
        {
            private readonly global::CodexMonitorAdapter codex = new global::CodexMonitorAdapter();
            private readonly global::RunBoardAdapter runboard = new global::RunBoardAdapter();

            public string Name { get { return "application-detect"; } }
            public string StartCommand { get { return string.Empty; } }
            public string StopCommand { get { return string.Empty; } }
            public string DetectedAppId { get; private set; }

            public bool IsApplicationTraffic(string line)
            {
                if (codex.IsApplicationTraffic(line)) { DetectedAppId = "codex-monitor"; return true; }
                if (runboard.IsApplicationTraffic(line)) { DetectedAppId = "runboard"; return true; }
                return false;
            }

            public bool IsReady(string line) { return codex.IsReady(line) || runboard.IsReady(line); }
            public bool IsStopped(string line) { return codex.IsStopped(line) || runboard.IsStopped(line); }
        }
    }
}
