using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

namespace IronCommander
{
    static class Program
    {
        [STAThread]
        static void Main(string[] args)
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string scriptPath = Path.Combine(baseDir, "hoi4_trainer.py");

            if (!File.Exists(scriptPath))
            {
                MessageBox.Show(
                    "Nie znaleziono pliku skryptu:\n" + scriptPath + "\n\nUpewnij się, że plik hoi4_trainer.py znajduje się w tym samym folderze.",
                    "HOI4 IronCommander — Błąd",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return;
            }

            string pythonExe = FindPythonExecutable();
            if (string.IsNullOrEmpty(pythonExe))
            {
                MessageBox.Show(
                    "Nie znaleziono interpretera Python (pythonw.exe / python.exe).\n\nZainstaluj Python 3 z oficjalnej strony python.org i upewnij się, że zaznaczono opcję 'Add Python to PATH'.",
                    "HOI4 IronCommander — Brak Pythona",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning
                );
                return;
            }

            try
            {
                ProcessStartInfo psi = new ProcessStartInfo();
                psi.FileName = pythonExe;
                string scriptArg = "\"" + scriptPath + "\"";
                if (args.Length > 0)
                {
                    psi.Arguments = scriptArg + " " + string.Join(" ", args);
                }
                else
                {
                    psi.Arguments = scriptArg;
                }
                psi.WorkingDirectory = baseDir;
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardError = true;

                Process proc = Process.Start(psi);
                if (proc != null && proc.WaitForExit(1200))
                {
                    if (proc.ExitCode != 0)
                    {
                        string err = proc.StandardError.ReadToEnd();
                        MessageBox.Show(
                            "Skrypt zakończył działanie z błędem (kod " + proc.ExitCode + "):\n\n" + err,
                            "HOI4 IronCommander — Błąd Uruchamiania",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Error
                        );
                    }
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Błąd podczas uruchamiania aplikacji:\n" + ex.Message,
                    "HOI4 IronCommander — Błąd",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
            }
        }

        static bool IsValidPythonBinary(string path)
        {
            try
            {
                if (!File.Exists(path)) return false;
                if (path.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) >= 0)
                {
                    FileInfo fi = new FileInfo(path);
                    if (fi.Length == 0) return false;
                }
                return true;
            }
            catch
            {
                return false;
            }
        }

        static string FindPythonExecutable()
        {
            // 1. Typowe lokalizacje pełnej instalacji Pythona użytkownika
            string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string pyDir = Path.Combine(localApp, "Programs", "Python");
            if (Directory.Exists(pyDir))
            {
                string[] dirs = Directory.GetDirectories(pyDir, "Python3*");
                Array.Sort(dirs);
                Array.Reverse(dirs);
                foreach (string sub in dirs)
                {
                    string candW = Path.Combine(sub, "pythonw.exe");
                    if (IsValidPythonBinary(candW)) return candW;
                    string candPy = Path.Combine(sub, "python.exe");
                    if (IsValidPythonBinary(candPy)) return candPy;
                }
            }

            // 2. Sprawdź pythonw.exe w PATH (pomijając puste aliasy WindowsApps)
            string pathEnv = Environment.GetEnvironmentVariable("PATH") ?? "";
            string[] paths = pathEnv.Split(Path.PathSeparator);

            foreach (string p in paths)
            {
                if (string.IsNullOrWhiteSpace(p)) continue;
                string w = Path.Combine(p.Trim(), "pythonw.exe");
                if (IsValidPythonBinary(w)) return w;
            }

            // 3. Sprawdź python.exe w PATH
            foreach (string p in paths)
            {
                if (string.IsNullOrWhiteSpace(p)) continue;
                string py = Path.Combine(p.Trim(), "python.exe");
                if (IsValidPythonBinary(py)) return py;
            }

            return null;
        }
    }
}
