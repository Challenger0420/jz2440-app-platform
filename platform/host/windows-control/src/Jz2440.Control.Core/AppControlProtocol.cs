using System;
using System.Collections.Generic;
using System.Linq;

namespace Jz2440.Control.Core
{
    public sealed class AppControlFrame
    {
        internal AppControlFrame(string type, IDictionary<string, string> fields)
        {
            Type = type;
            Fields = new Dictionary<string, string>(fields, StringComparer.Ordinal);
        }

        public string Type { get; private set; }
        public IReadOnlyDictionary<string, string> Fields { get; private set; }

        public string Get(string key)
        {
            string value;
            return Fields.TryGetValue(key, out value) ? value : null;
        }
    }

    public static class AppControlProtocol
    {
        public static bool TryParse(string frame, out AppControlFrame parsed, out string error)
        {
            parsed = null;
            error = null;
            if (string.IsNullOrEmpty(frame) || frame.Length > 255 || !frame.EndsWith("\n", StringComparison.Ordinal))
            {
                error = "invalid frame length or terminator";
                return false;
            }

            string body = frame.Substring(0, frame.Length - 1);
            if (body.Length < 8 || body[0] != '<' || body[body.Length - 1] != '>')
            {
                error = "invalid frame envelope";
                return false;
            }

            string[] tokens = body.Substring(1, body.Length - 2).Split('|');
            if (tokens.Length < 2 || tokens[0] != "APP")
            {
                error = "unsupported frame family";
                return false;
            }

            Dictionary<string, string> fields = new Dictionary<string, string>(StringComparer.Ordinal);
            for (int index = 2; index < tokens.Length; index++)
            {
                int equals = tokens[index].IndexOf('=');
                if (equals <= 0 || equals == tokens[index].Length - 1)
                {
                    error = "invalid field";
                    return false;
                }

                string key = tokens[index].Substring(0, equals);
                string value = tokens[index].Substring(equals + 1);
                if (fields.ContainsKey(key) || !IsSafeToken(key) || !IsSafeToken(value))
                {
                    error = "duplicate or unsafe field";
                    return false;
                }
                fields.Add(key, value);
            }

            string type = tokens[1];
            if (!IsSafeToken(type))
            {
                error = "invalid frame type";
                return false;
            }
            parsed = new AppControlFrame(type, fields);
            return true;
        }

        public static string EncodeRequest(int id, string operation, string appId)
        {
            if (id < 0) throw new ArgumentOutOfRangeException("id");
            RequireSafe(operation, "operation");
            RequireSafe(appId, "appId");
            return string.Format("<APP|REQ|ID={0}|OP={1}|NAME={2}>\n", id, operation, appId);
        }

        private static bool IsSafeToken(string value)
        {
            return !string.IsNullOrEmpty(value) && value.All(character =>
                char.IsLetterOrDigit(character) || "_.:/=-,".IndexOf(character) >= 0);
        }

        private static void RequireSafe(string value, string name)
        {
            if (!IsSafeToken(value)) throw new ArgumentException("Unsafe protocol token.", name);
        }
    }
}
