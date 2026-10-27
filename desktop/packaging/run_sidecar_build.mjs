import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const packagingDirectory = path.dirname(fileURLToPath(import.meta.url));
const desktopDirectory = path.resolve(packagingDirectory, "..");
const repositoryRoot = path.resolve(desktopDirectory, "..");
const pythonVersion = readFileSync(path.join(repositoryRoot, ".python-version"), "utf8").trim();

function runUv(args, options = {}) {
  const result = spawnSync("uv", args, { stdio: "inherit", ...options });
  if (result.error) {
    console.error(result.error.message);
    return 1;
  }
  return result.status ?? 1;
}

function findVsDevCommand() {
  const programFilesX86 = process.env["ProgramFiles(x86)"];
  if (!programFilesX86) return null;

  const vswhere = path.join(programFilesX86, "Microsoft Visual Studio", "Installer", "vswhere.exe");
  if (!existsSync(vswhere)) return null;

  const result = spawnSync(
    vswhere,
    [
      "-latest",
      "-products",
      "*",
      "-requires",
      "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
      "-property",
      "installationPath",
    ],
    { encoding: "utf8" },
  );
  if (result.error || result.status !== 0) return null;

  const installationPath = result.stdout.trim();
  if (!installationPath) return null;

  const command = path.join(installationPath, "Common7", "Tools", "VsDevCmd.bat");
  return existsSync(command) ? command : null;
}

function hasMsvcEnvironment() {
  if (!process.env.WindowsSDKVersion) return false;
  return ["cl", "link"].every((executable) => {
    const result = spawnSync("where.exe", [`${executable}.exe`], { stdio: "ignore" });
    return !result.error && result.status === 0;
  });
}

function enterMsvcEnvironment() {
  const vsDevCommand = findVsDevCommand();
  if (!vsDevCommand) {
    console.error(
      "Windows sidecar builds require Visual Studio Build Tools with the x64 C++ tools and Windows SDK.",
    );
    return 1;
  }

  const scriptPath = fileURLToPath(import.meta.url);
  const environment = {
    ...process.env,
    AEGIS_SIDECAR_MSVC_READY: "1",
    AEGIS_SIDECAR_NODE: process.execPath,
    AEGIS_SIDECAR_RUNNER: scriptPath,
    AEGIS_SIDECAR_VSDEV_COMMAND: vsDevCommand,
  };
  const command = [
    'call "%AEGIS_SIDECAR_VSDEV_COMMAND%" -no_logo -arch=x64 -host_arch=x64 >nul',
    'if defined WindowsSDKVersion (where cl.exe >nul 2>&1 && where link.exe >nul 2>&1 && "%AEGIS_SIDECAR_NODE%" "%AEGIS_SIDECAR_RUNNER%") else (echo Visual Studio did not activate a Windows SDK. 1>&2 & exit /b 1)',
  ].join(" && ");
  const result = spawnSync(process.env.ComSpec || "cmd.exe", ["/d", "/s", "/c", command], {
    cwd: repositoryRoot,
    env: environment,
    stdio: "inherit",
    windowsVerbatimArguments: true,
  });
  if (result.error) {
    console.error(result.error.message);
    return 1;
  }
  return result.status ?? 1;
}

if (
  process.platform === "win32" &&
  process.env.AEGIS_SIDECAR_MSVC_READY !== "1" &&
  !hasMsvcEnvironment()
) {
  process.exit(enterMsvcEnvironment());
}

const pythonResult = spawnSync("uv", ["python", "find", "--system", pythonVersion], {
  cwd: repositoryRoot,
  encoding: "utf8",
});
if (pythonResult.error || pythonResult.status !== 0) {
  if (pythonResult.stdout) process.stdout.write(pythonResult.stdout);
  if (pythonResult.stderr) process.stderr.write(pythonResult.stderr);
  if (pythonResult.error) console.error(pythonResult.error.message);
  process.exit(pythonResult.status || 1);
}

const python = pythonResult.stdout.trim();
if (!python) {
  console.error(`uv did not return a system Python path for ${pythonVersion}`);
  process.exit(1);
}

const environment = {
  ...process.env,
  UV_PROJECT_ENVIRONMENT: path.join(repositoryRoot, ".venv-desktop-sidecar"),
};
const syncStatus = runUv(
  [
    "sync",
    "--project",
    repositoryRoot,
    "--python",
    python,
    "--locked",
    "--no-default-groups",
    "--no-editable",
    "--reinstall-package",
    "aegis-cognition",
    "--extra",
    "all",
    "--extra",
    "dev",
    "--group",
    "desktop",
  ],
  { cwd: repositoryRoot, env: environment },
);
if (syncStatus !== 0) process.exit(syncStatus);

process.exit(
  runUv(
    [
      "run",
      "--project",
      repositoryRoot,
      "--python",
      python,
      "--locked",
      "--no-sync",
      "python",
      path.join(packagingDirectory, "build_sidecar.py"),
      "--build-only",
    ],
    { cwd: desktopDirectory, env: environment },
  ),
);
