using System;
using System.Collections.Generic;

namespace Jz2440.Control.Core
{
    public enum DeviceConnectionState
    {
        Disconnected,
        Connecting,
        Connected,
        Error
    }

    public enum AppState
    {
        Unknown,
        Available,
        Unavailable,
        Starting,
        Running,
        Stopping,
        Failed
    }

    public sealed class AppDescriptor
    {
        public AppDescriptor(string id, string displayName, bool available, IApplicationAdapter adapter)
        {
            Id = id;
            DisplayName = displayName;
            Available = available;
            Adapter = adapter;
        }

        public string Id { get; private set; }
        public string DisplayName { get; private set; }
        public bool Available { get; internal set; }
        public IApplicationAdapter Adapter { get; private set; }
    }

    public sealed class AppCardSnapshot
    {
        public string Id { get; set; }
        public string DisplayName { get; set; }
        public AppState State { get; set; }
        public bool IsCurrent { get; set; }
        public bool CanLaunch { get; set; }
        public string ErrorMessage { get; set; }
    }

    public sealed class DeviceSnapshot
    {
        public DeviceConnectionState ConnectionState { get; set; }
        public string PortName { get; set; }
        public string CurrentAppId { get; set; }
        public string Message { get; set; }
        public bool IsDevelopmentMock { get; set; }
        public IReadOnlyList<AppCardSnapshot> Apps { get; set; }
    }

    public sealed class SwitchResult
    {
        public bool Success { get; set; }
        public string AppId { get; set; }
        public string ErrorCode { get; set; }
        public string Message { get; set; }
    }

    public sealed class DeviceSnapshotEventArgs : EventArgs
    {
        public DeviceSnapshotEventArgs(DeviceSnapshot snapshot)
        {
            Snapshot = snapshot;
        }

        public DeviceSnapshot Snapshot { get; private set; }
    }
}
