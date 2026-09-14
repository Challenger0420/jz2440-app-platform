using System;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

namespace Jz2440.Control.Core
{
    // The physical SerialPort has exactly one reader. BoardController and an
    // active application provider consume data from this buffer in turn.
    internal sealed class BufferedSerialTransport : global::ISerialTransport
    {
        private readonly global::ISerialTransport inner;
        private readonly Action<Exception> faulted;
        private readonly object gate = new object();
        private readonly StringBuilder buffer = new StringBuilder();
        private readonly CancellationTokenSource cancellation = new CancellationTokenSource();
        private readonly Task pump;
        private bool disposed;
        private bool hasFault;

        public BufferedSerialTransport(global::ISerialTransport transport, Action<Exception> onFault)
        {
            inner = transport ?? throw new ArgumentNullException("transport");
            faulted = onFault;
            pump = Task.Run(Pump);
        }

        public string PortName { get { return inner.PortName; } }
        public bool IsFaulted { get { lock (gate) return hasFault; } }

        public void Write(string frame)
        {
            if (disposed) throw new ObjectDisposedException(GetType().Name);
            inner.Write(frame);
        }

        public string ReadAvailable()
        {
            lock (gate)
            {
                string value = buffer.ToString();
                buffer.Length = 0;
                return value;
            }
        }

        public void PushBack(string incoming)
        {
            if (string.IsNullOrEmpty(incoming)) return;
            lock (gate)
            {
                string existing = buffer.ToString();
                buffer.Length = 0;
                buffer.Append(incoming);
                buffer.Append(existing);
            }
        }

        public void Dispose()
        {
            if (disposed) return;
            disposed = true;
            cancellation.Cancel();
            try { inner.Dispose(); } catch { }
            try { pump.Wait(1000); } catch { }
            cancellation.Dispose();
        }

        private void Pump()
        {
            try
            {
                while (!cancellation.IsCancellationRequested)
                {
                    string incoming = inner.ReadAvailable();
                    if (!string.IsNullOrEmpty(incoming))
                    {
                        lock (gate)
                        {
                            buffer.Append(incoming);
                            if (buffer.Length > 8192)
                                buffer.Remove(0, buffer.Length - 4096);
                        }
                    }
                    Thread.Sleep(25);
                }
            }
            catch (Exception error)
            {
                if (cancellation.IsCancellationRequested) return;
                lock (gate) hasFault = true;
                if (faulted != null) faulted(error);
            }
        }
    }
}
