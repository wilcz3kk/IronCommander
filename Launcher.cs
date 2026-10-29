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
                    "Nie znaleziono interpretera Python (pythonw.exe / python.exe).\n\nZainstaluj Python 3 z oficjalnej strony python.org lub Microsoft Store i upewnij się, że zaznaczono opcję 'Add Python to PATH'.",
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

                Process.Start(psi);
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

        static string FindPythonExecutable()
        {
            // 1. Sprawdź pythonw.exe w PATH
            string pathEnv = Environment.GetEnvironmentVariable("PATH") ?? "";
            string[] paths = pathEnv.Split(Path.PathSeparator);

            foreach (string p in paths)
            {
                if (string.IsNullOrWhiteSpace(p)) continue;
                string w = Path.Combine(p.Trim(), "pythonw.exe");
                if (File.Exists(w)) return w;
            }

            // 2. Sprawdź python.exe w PATH
            foreach (string p in paths)
            {
                if (string.IsNullOrWhiteSpace(p)) continue;
                string py = Path.Combine(p.Trim(), "python.exe");
                if (File.Exists(py)) return py;
            }

            // 3. Typowe lokalizacje instalacji użytkownika
            string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string pyDir = Path.Combine(localApp, "Programs", "Python");
            if (Directory.Exists(pyDir))
            {
                foreach (string sub in Directory.GetDirectories(pyDir, "Python3*"))
                {
                    string candW = Path.Combine(sub, "pythonw.exe");
                    if (File.Exists(candW)) return candW;
                    string candPy = Path.Combine(sub, "python.exe");
                    if (File.Exists(candPy)) return candPy;
                }
            }

            return null;
        }
    }
}
