namespace Jz2440.Control.Core
{
    public interface IApplicationAdapter
    {
        string AppId { get; }
        bool CanStart { get; }
        string StartCommand { get; }
        string StopFrame { get; }
        bool IsApplicationTraffic(string line);
        bool IsReady(string line);
        bool IsStopped(string line);
    }

    public sealed class CodexMonitorApplicationAdapter : IApplicationAdapter
    {
        private readonly global::CodexMonitorAdapter legacyAdapter = new global::CodexMonitorAdapter();

        public string AppId { get { return legacyAdapter.Name; } }
        public bool CanStart { get { return true; } }
        public string StartCommand { get { return legacyAdapter.StartCommand; } }
        public string StopFrame { get { return legacyAdapter.StopCommand; } }
        public bool IsApplicationTraffic(string line) { return legacyAdapter.IsApplicationTraffic(line); }
        public bool IsReady(string line) { return legacyAdapter.IsReady(line); }
        public bool IsStopped(string line) { return legacyAdapter.IsStopped(line); }
    }

    public sealed class QtopiaApplicationAdapter : IApplicationAdapter
    {
        public string AppId { get { return "qtopia"; } }
        public bool CanStart { get { return true; } }
        public string StartCommand { get { return string.Empty; } }
        public string StopFrame { get { return string.Empty; } }
        public bool IsApplicationTraffic(string line) { return false; }
        public bool IsReady(string line) { return false; }
        public bool IsStopped(string line) { return false; }
    }

    public sealed class RunBoardApplicationAdapter : IApplicationAdapter
    {
        public string AppId { get { return "runboard"; } }
        public bool CanStart { get { return true; } }
        private readonly global::RunBoardAdapter legacyAdapter = new global::RunBoardAdapter();
        public string StartCommand { get { return legacyAdapter.StartCommand; } }
        public string StopFrame { get { return legacyAdapter.StopCommand; } }
        public bool IsApplicationTraffic(string line) { return legacyAdapter.IsApplicationTraffic(line); }
        public bool IsReady(string line) { return legacyAdapter.IsReady(line); }
        public bool IsStopped(string line) { return legacyAdapter.IsStopped(line); }
    }
}
