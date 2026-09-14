using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace Jz2440.Control.Core
{
    public interface IDeviceConnection : IDisposable
    {
        event EventHandler<DeviceSnapshotEventArgs> SnapshotChanged;
        DeviceSnapshot Snapshot { get; }
        Task<IReadOnlyList<string>> DiscoverPortsAsync(CancellationToken cancellationToken);
        Task ConnectAsync(string portName, CancellationToken cancellationToken);
        Task DisconnectAsync();
        Task<SwitchResult> SwitchAsync(string appId, CancellationToken cancellationToken);
    }
}
