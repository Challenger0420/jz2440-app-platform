using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace Jz2440.Control.Core
{
    public sealed class MockDeviceConnection : IDeviceConnection
    {
        private readonly AppRegistry registry;
        private readonly ILogger logger;
        private readonly SemaphoreSlim switchLock = new SemaphoreSlim(1, 1);
        private DeviceConnectionState connectionState = DeviceConnectionState.Disconnected;
        private string portName = "Mock backend";
        private string message = "Development mock backend is ready.";
        private bool failNextSwitch;
        private bool disposed;

        public MockDeviceConnection(AppRegistry appRegistry, ILogger appLogger)
        {
            registry = appRegistry ?? throw new ArgumentNullException("appRegistry");
            logger = appLogger ?? new NullLogger();
        }

        public event EventHandler<DeviceSnapshotEventArgs> SnapshotChanged;

        public DeviceSnapshot Snapshot { get { return CreateSnapshot(); } }

        public Task<IReadOnlyList<string>> DiscoverPortsAsync(CancellationToken cancellationToken)
        {
            ThrowIfDisposed();
            return Task.FromResult<IReadOnlyList<string>>(new[] { "Mock backend" });
        }

        public async Task ConnectAsync(string requestedPortName, CancellationToken cancellationToken)
        {
            ThrowIfDisposed();
            connectionState = DeviceConnectionState.Connecting;
            message = "Connecting to development mock...";
            Publish();
            await Task.Delay(120, cancellationToken).ConfigureAwait(false);
            cancellationToken.ThrowIfCancellationRequested();
            portName = string.IsNullOrWhiteSpace(requestedPortName) ? "Mock backend" : requestedPortName;
            registry.SetCurrent("qtopia");
            connectionState = DeviceConnectionState.Connected;
            message = "Connected · development mock";
            logger.Info("Mock connection established.");
            Publish();
        }

        public Task DisconnectAsync()
        {
            ThrowIfDisposed();
            connectionState = DeviceConnectionState.Disconnected;
            registry.SetUnknown();
            message = "Disconnected · development mock";
            Publish();
            return Task.CompletedTask;
        }

        public async Task<SwitchResult> SwitchAsync(string appId, CancellationToken cancellationToken)
        {
            ThrowIfDisposed();
            if (connectionState != DeviceConnectionState.Connected)
                return Failure(appId, "NOT_CONNECTED", "Device is not connected.");

            if (!await switchLock.WaitAsync(0, cancellationToken).ConfigureAwait(false))
                return Failure(appId, "SWITCHING", "Another application switch is already running.");

            try
            {
                AppCardSnapshot target = Find(appId);
                if (!target.CanLaunch)
                    return Failure(appId, "UNAVAILABLE", "Application is unavailable.");
                if (registry.CurrentAppId == appId)
                    return Success(appId);

                string oldAppId = registry.CurrentAppId;
                if (!string.IsNullOrEmpty(oldAppId))
                {
                    registry.BeginStop(oldAppId);
                    message = "Stopping current application...";
                    Publish();
                    await Task.Delay(90, cancellationToken).ConfigureAwait(false);
                    registry.CompleteStop(oldAppId);
                }

                registry.BeginStart(appId);
                message = "Starting " + appId + "...";
                Publish();
                await Task.Delay(150, cancellationToken).ConfigureAwait(false);
                if (failNextSwitch)
                {
                    failNextSwitch = false;
                    registry.Fail(appId, "Mock start failure.");
                    message = "Failed to start " + appId + ".";
                    Publish();
                    return Failure(appId, "START_FAILED", message);
                }

                registry.CompleteStart(appId);
                message = "Running " + appId + " · development mock";
                Publish();
                return Success(appId);
            }
            catch (OperationCanceledException)
            {
                connectionState = DeviceConnectionState.Error;
                registry.SetUnknown();
                message = "Switch cancelled; reconnect required.";
                Publish();
                throw;
            }
            finally
            {
                switchLock.Release();
            }
        }

        public void SetCurrentForDevelopment(string appId)
        {
            ThrowIfDisposed();
            registry.SetCurrentForDevelopment(appId);
            message = "Mock current app set to " + appId + ".";
            Publish();
        }

        public void FailNextSwitchForDevelopment()
        {
            ThrowIfDisposed();
            failNextSwitch = true;
        }

        public void DisconnectForDevelopment()
        {
            if (!disposed) DisconnectAsync().GetAwaiter().GetResult();
        }

        public void Dispose()
        {
            if (disposed) return;
            disposed = true;
            switchLock.Dispose();
        }

        private AppCardSnapshot Find(string appId)
        {
            foreach (AppCardSnapshot item in registry.Snapshot())
                if (item.Id == appId) return item;
            return new AppCardSnapshot { Id = appId, State = AppState.Unknown, CanLaunch = false };
        }

        private SwitchResult Success(string appId)
        {
            return new SwitchResult { Success = true, AppId = appId, Message = "Switch completed." };
        }

        private SwitchResult Failure(string appId, string code, string text)
        {
            return new SwitchResult { Success = false, AppId = appId, ErrorCode = code, Message = text };
        }

        private DeviceSnapshot CreateSnapshot()
        {
            return new DeviceSnapshot
            {
                ConnectionState = connectionState,
                PortName = portName,
                CurrentAppId = registry.CurrentAppId,
                Message = message,
                IsDevelopmentMock = true,
                Apps = registry.Snapshot()
            };
        }

        private void Publish()
        {
            SnapshotChanged?.Invoke(this, new DeviceSnapshotEventArgs(CreateSnapshot()));
        }

        private void ThrowIfDisposed()
        {
            if (disposed) throw new ObjectDisposedException(GetType().Name);
        }
    }
}
