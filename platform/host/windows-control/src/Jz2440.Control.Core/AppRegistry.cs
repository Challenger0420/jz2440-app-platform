using System;
using System.Collections.Generic;
using System.Linq;

namespace Jz2440.Control.Core
{
    public sealed class AppRegistry
    {
        private sealed class RuntimeEntry
        {
            public AppDescriptor Descriptor;
            public AppState State;
            public string ErrorMessage;
        }

        private readonly List<RuntimeEntry> entries;
        private string currentAppId;

        public AppRegistry(IEnumerable<AppDescriptor> descriptors)
        {
            if (descriptors == null) throw new ArgumentNullException("descriptors");
            entries = descriptors.Select(descriptor => new RuntimeEntry
            {
                Descriptor = descriptor,
                State = descriptor.Available ? AppState.Available : AppState.Unavailable
            }).ToList();
            if (entries.Count == 0) throw new ArgumentException("At least one application is required.", "descriptors");
        }

        public string CurrentAppId { get { return currentAppId; } }

        public static AppRegistry CreateDefault()
        {
            return new AppRegistry(new[]
            {
                new AppDescriptor("runboard", "RunBoard", false, new RunBoardApplicationAdapter()),
                new AppDescriptor("codex-monitor", "Codex Monitor", true, new CodexMonitorApplicationAdapter()),
                new AppDescriptor("qtopia", "Qtopia", true, new QtopiaApplicationAdapter())
            });
        }

        public IReadOnlyList<AppCardSnapshot> Snapshot()
        {
            return entries.Select(entry => new AppCardSnapshot
            {
                Id = entry.Descriptor.Id,
                DisplayName = entry.Descriptor.DisplayName,
                State = entry.State,
                IsCurrent = entry.Descriptor.Id == currentAppId,
                CanLaunch = entry.Descriptor.Adapter.CanStart &&
                            (entry.State == AppState.Available || entry.State == AppState.Failed),
                ErrorMessage = entry.ErrorMessage
            }).ToList();
        }

        public AppDescriptor Get(string appId)
        {
            RuntimeEntry entry = entries.FirstOrDefault(item => item.Descriptor.Id == appId);
            return entry == null ? null : entry.Descriptor;
        }

        public AppState GetState(string appId)
        {
            return Find(appId).State;
        }

        public void SetAvailable(string appId, bool available, string message = null)
        {
            RuntimeEntry entry = Find(appId);
            entry.Descriptor.Available = available;
            if (entry.State != AppState.Running && entry.State != AppState.Starting && entry.State != AppState.Stopping)
                entry.State = available ? AppState.Available : AppState.Unavailable;
            if (!string.IsNullOrEmpty(message)) entry.ErrorMessage = message;
            else if (available) entry.ErrorMessage = null;
        }

        public void SetCurrent(string appId)
        {
            RuntimeEntry target = Find(appId);
            foreach (RuntimeEntry entry in entries)
            {
                entry.ErrorMessage = null;
                entry.State = entry.Descriptor.Available ? AppState.Available : AppState.Unavailable;
            }
            target.State = AppState.Running;
            currentAppId = appId;
        }

        public void SetUnknown()
        {
            currentAppId = null;
            foreach (RuntimeEntry entry in entries)
            {
                entry.ErrorMessage = null;
                entry.State = entry.Descriptor.Available ? AppState.Unknown : AppState.Unavailable;
            }
        }

        public void BeginStart(string appId)
        {
            RuntimeEntry entry = Find(appId);
            if (!entry.Descriptor.Available || !entry.Descriptor.Adapter.CanStart)
                throw new InvalidOperationException("Application is unavailable: " + appId);
            if (entry.State != AppState.Available && entry.State != AppState.Failed)
                throw new InvalidOperationException("Application is not startable from state " + entry.State + ".");
            entry.ErrorMessage = null;
            entry.State = AppState.Starting;
        }

        public void CompleteStart(string appId)
        {
            RuntimeEntry target = Find(appId);
            foreach (RuntimeEntry entry in entries)
            {
                if (entry.Descriptor.Id != appId && entry.State == AppState.Running)
                    entry.State = entry.Descriptor.Available ? AppState.Available : AppState.Unavailable;
            }
            target.State = AppState.Running;
            target.ErrorMessage = null;
            currentAppId = appId;
        }

        public void BeginStop(string appId)
        {
            RuntimeEntry entry = Find(appId);
            if (entry.State != AppState.Running)
                throw new InvalidOperationException("Application is not running: " + appId);
            entry.State = AppState.Stopping;
        }

        public void CompleteStop(string appId)
        {
            RuntimeEntry entry = Find(appId);
            entry.State = entry.Descriptor.Available ? AppState.Available : AppState.Unavailable;
            entry.ErrorMessage = null;
            if (currentAppId == appId) currentAppId = null;
        }

        public void Fail(string appId, string message)
        {
            RuntimeEntry entry = Find(appId);
            entry.State = AppState.Failed;
            entry.ErrorMessage = message;
            if (currentAppId == appId) currentAppId = null;
        }

        public void SetCurrentForDevelopment(string appId)
        {
            SetCurrent(appId);
        }

        private RuntimeEntry Find(string appId)
        {
            RuntimeEntry entry = entries.FirstOrDefault(item => item.Descriptor.Id == appId);
            if (entry == null) throw new ArgumentException("Unknown application: " + appId, "appId");
            return entry;
        }
    }
}
