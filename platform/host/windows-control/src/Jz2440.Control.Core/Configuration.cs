using System;
using System.IO;
using System.Text.Json;

namespace Jz2440.Control.Core
{
    public sealed class AppConfiguration
    {
        public string BackendMode { get; set; } = "serial";
        public string PreferredPort { get; set; }
        public string[] AllowedHardwareIds { get; set; } = new[] { "USB\\VID_067B&PID_2303" };
        public int BaudRate { get; set; } = 115200;
        public int OperationTimeoutMs { get; set; } = 5000;
        public int ReconnectMinMs { get; set; } = 1000;
        public int ReconnectMaxMs { get; set; } = 10000;
        public string CodexExecutable { get; set; }
        public string RunBoardPython { get; set; } = "python";
        public string RunBoardStateScript { get; set; }
        public int RunBoardFrameTimeoutMs { get; set; } = 60000;
        public int RunBoardFrameIntervalMs { get; set; } = 60000;
    }

    public static class ConfigurationStore
    {
        // Minimal, user-visible explanation of the last load. It stays null
        // when a usable local configuration was read.
        public static string LastLoadDiagnostic { get; private set; }
        public static string LastLoadedPath { get; private set; }

        public static AppConfiguration Load()
        {
            LastLoadedPath = null;
            LastLoadDiagnostic = null;
            string primaryPath = GetPath();
            string fallbackPath = GetExecutableSidecarPath();
            string[] candidates = string.Equals(primaryPath, fallbackPath, StringComparison.OrdinalIgnoreCase)
                ? new[] { primaryPath }
                : new[] { primaryPath, fallbackPath };
            string lastDiagnostic = null;

            foreach (string path in candidates)
            {
                if (!File.Exists(path))
                {
                    lastDiagnostic = "config file not found: " + path;
                    continue;
                }
                try
                {
                    AppConfiguration configuration = JsonSerializer.Deserialize<AppConfiguration>(File.ReadAllText(path));
                    if (configuration == null) configuration = new AppConfiguration();
                    // A desktop launch can occasionally see a stale or empty
                    // LocalAppData view. Prefer a complete sidecar config in
                    // that case, while keeping LocalAppData as the primary
                    // location for normal installs.
                    if (string.IsNullOrWhiteSpace(configuration.RunBoardStateScript) &&
                        !string.Equals(path, fallbackPath, StringComparison.OrdinalIgnoreCase))
                    {
                        lastDiagnostic = "config file has no RunBoardStateScript: " + path;
                        continue;
                    }
                    LastLoadedPath = path;
                    LastLoadDiagnostic = null;
                    return configuration;
                }
                catch (Exception error)
                {
                    lastDiagnostic = "config file unreadable: " + path + " (" + error.GetType().Name + ": " + error.Message + ")";
                }
            }

            LastLoadDiagnostic = lastDiagnostic ?? "config file not found: " + primaryPath;
            return new AppConfiguration();
        }

        public static string GetPath()
        {
            string localAppData = Environment.GetEnvironmentVariable("LOCALAPPDATA");
            if (string.IsNullOrWhiteSpace(localAppData))
                localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (string.IsNullOrWhiteSpace(localAppData))
                localAppData = AppContext.BaseDirectory;
            return Path.Combine(localAppData, "JZ2440Control", "config.json");
        }

        public static string GetExecutableSidecarPath()
        {
            return Path.Combine(AppContext.BaseDirectory, "JZ2440Control.config.json");
        }
    }
}
