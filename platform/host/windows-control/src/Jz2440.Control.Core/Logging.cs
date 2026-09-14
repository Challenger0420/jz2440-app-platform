using System;
using System.IO;

namespace Jz2440.Control.Core
{
    public interface ILogger
    {
        void Info(string message);
        void Error(string message, Exception error = null);
    }

    public sealed class FileLogger : ILogger
    {
        private readonly object gate = new object();
        private readonly string directory;

        public FileLogger()
        {
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            directory = Path.Combine(localAppData, "JZ2440Control", "logs");
        }

        public void Info(string message) { Write("INFO", message, null); }
        public void Error(string message, Exception error = null) { Write("ERROR", message, error); }

        private void Write(string level, string message, Exception error)
        {
            try
            {
                lock (gate)
                {
                    Directory.CreateDirectory(directory);
                    string path = Path.Combine(directory, "control.log");
                    string suffix = string.Empty;
                    if (error != null)
                    {
                        string detail = (error.Message ?? string.Empty)
                            .Replace('\r', ' ')
                            .Replace('\n', ' ');
                        suffix = " " + error.GetType().Name +
                            (detail.Length == 0 ? string.Empty : ": " + detail);
                    }
                    File.AppendAllText(path, string.Format("{0:O} [{1}] {2}{3}{4}",
                        DateTimeOffset.Now, level, message, suffix, Environment.NewLine));
                }
            }
            catch
            {
                // Logging must not prevent the control UI from running.
            }
        }
    }

    public sealed class NullLogger : ILogger
    {
        public void Info(string message) { }
        public void Error(string message, Exception error = null) { }
    }
}
