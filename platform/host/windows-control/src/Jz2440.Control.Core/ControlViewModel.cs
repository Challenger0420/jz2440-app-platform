using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace Jz2440.Control.Core
{
    public sealed class ControlViewModel : IDisposable
    {
        private readonly IDeviceConnection device;
        private readonly AppRegistry registry;
        private readonly ILogger logger;
        private readonly SemaphoreSlim switchLock = new SemaphoreSlim(1, 1);
        private DeviceSnapshot snapshot;
        private bool disposed;

        public ControlViewModel(IDeviceConnection connection, AppRegistry appRegistry, ILogger appLogger)
        {
            device = connection ?? throw new ArgumentNullException("connection");
            registry = appRegistry ?? throw new ArgumentNullException("appRegistry");
            logger = appLogger ?? new NullLogger();
            snapshot = device.Snapshot;
            device.SnapshotChanged += DeviceOnSnapshotChanged;
        }

        public event EventHandler Changed;
        public DeviceSnapshot Snapshot { get { return snapshot; } }
        public IReadOnlyList<AppCardSnapshot> Apps { get { return snapshot.Apps; } }
        public bool IsSwitching { get; private set; }

        public Task<IReadOnlyList<string>> DiscoverPortsAsync(CancellationToken cancellationToken)
        {
            ThrowIfDisposed();
            return device.DiscoverPortsAsync(cancellationToken);
        }

        public async Task ConnectAsync(string portName, CancellationToken cancellationToken)
        {
            ThrowIfDisposed();
            await device.ConnectAsync(portName, cancellationToken).ConfigureAwait(false);
        }

        public Task DisconnectAsync()
        {
            ThrowIfDisposed();
            return device.DisconnectAsync();
        }

        public async Task<SwitchResult> LaunchAsync(string appId, CancellationToken cancellationToken)
        {
            ThrowIfDisposed();
            if (!await switchLock.WaitAsync(0, cancellationToken).ConfigureAwait(false))
                return new SwitchResult { AppId = appId, ErrorCode = "SWITCHING", Message = "Another switch is already running." };
            try
            {
                IsSwitching = true;
                Changed?.Invoke(this, EventArgs.Empty);
                AppCardSnapshot target = Apps.FirstOrDefault(item => item.Id == appId);
                if (target == null || !target.CanLaunch)
                    return new SwitchResult { AppId = appId, ErrorCode = "UNAVAILABLE", Message = "Application is unavailable." };
                return await device.SwitchAsync(appId, cancellationToken).ConfigureAwait(false);
            }
            catch (Exception error)
            {
                logger.Error("Application launch request failed.", error);
                return new SwitchResult { AppId = appId, ErrorCode = "REQUEST_FAILED", Message = error.Message };
            }
            finally
            {
                IsSwitching = false;
                Changed?.Invoke(this, EventArgs.Empty);
                switchLock.Release();
            }
        }

        public void Dispose()
        {
            if (disposed) return;
            disposed = true;
            device.SnapshotChanged -= DeviceOnSnapshotChanged;
            device.Dispose();
            switchLock.Dispose();
        }

        private void DeviceOnSnapshotChanged(object sender, DeviceSnapshotEventArgs args)
        {
            snapshot = args.Snapshot;
            Changed?.Invoke(this, EventArgs.Empty);
        }

        private void ThrowIfDisposed()
        {
            if (disposed) throw new ObjectDisposedException(GetType().Name);
        }
    }
}
