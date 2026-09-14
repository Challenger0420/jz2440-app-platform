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
        public string RunBoardPython { get; set; } = "python";
        public string RunBoardStateScript { get; set; }
        public int RunBoardFrameIntervalMs { get; set; } = 60000;
    }

    public static class ConfigurationStore
    {
        public static AppConfiguration Load()
        {
            string path = GetPath();
            if (!File.Exists(path)) return new AppConfiguration();
            try
            {
                AppConfiguration configuration = JsonSerializer.Deserialize<AppConfiguration>(File.ReadAllText(path));
                return configuration ?? new AppConfiguration();
            }
            catch
            {
                return new AppConfiguration();
            }
        }

        public static string GetPath()
        {
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            return Path.Combine(localAppData, "JZ2440Control", "config.json");
        }
    }
}
