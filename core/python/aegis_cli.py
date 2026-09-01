import sys
import os
import re
from contextlib import suppress
import yaml
from pathlib import Path

PROVIDER_REGEXES = {
    "OpenAI": re.compile(r"^sk-(?:proj-)?[A-Za-z0-9\-_]{40,}$"),
    "Anthropic": re.compile(r"^sk-ant-[a-zA-Z0-9\-_]{40,}$"),
    "Gemini": re.compile(r"^AIza[0-9A-Za-z_\-]{35}$"),
    "Groq": re.compile(r"^gsk_[A-Za-z0-9]{36}$"),
    "Local": re.compile(r"^(?:http://(?:localhost|127\.0\.0\.1):\d{4,5}(?:/v1)?|ollama)$")
}

def get_masked_input(prompt: str) -> str:
    print(prompt, end='', flush=True)
    password = []
    if os.name == 'nt':
        import msvcrt
        while True:
            char = msvcrt.getwch()
            if char in ('\r', '\n'):
                print('')
                break
            elif char == '\b':
                if password:
                    password.pop()
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
            elif char in ('\x00', '\xe0'):
                msvcrt.getwch() # Swallow special keys
            else:
                password.append(char)
                sys.stdout.write('*')
                sys.stdout.flush()
    else:
        if not sys.stdin.isatty():
            import getpass
            return getpass.getpass("")
        import termios
        import tty
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(sys.stdin.fileno())
            while True:
                char = sys.stdin.read(1)
                if char in ('\r', '\n'):
                    sys.stdout.write('\r\n')
                    break
                elif char in ('\b', '\x7f'):
                    if password:
                        password.pop()
                        sys.stdout.write('\b \b')
                        sys.stdout.flush()
                elif char == '\x03': # Ctrl+C
                    raise KeyboardInterrupt
                else:
                    password.append(char)
                    sys.stdout.write('*')
                    sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    return ''.join(password)

def detect_provider(api_key: str) -> str | None:
    for provider, pattern in PROVIDER_REGEXES.items():
        if pattern.match(api_key):
            return provider
    return None

def get_aegis_dir() -> Path:
    home = Path.home()
    aegis_dir = home / ".aegis"
    aegis_dir.mkdir(parents=True, exist_ok=True)
    return aegis_dir

def save_config(provider: str, api_key: str, trust_level: str, enable_browser: bool):
    aegis_dir = get_aegis_dir()
    config_path = aegis_dir / "config.yaml"
    env_path = aegis_dir / ".env"
    
    config_data = {
        "provider": provider.lower(),
        "trust_level": trust_level,
        "tools": {
            "browser_automation": enable_browser
        }
    }
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f)
        
    env_var_name = f"{provider.upper()}_API_KEY" if provider != "Local" else "LOCAL_API_URL"
    with open(env_path, "w", encoding="utf-8") as f:
        f.write(f'{env_var_name}="{api_key}"\n')
        
    with suppress(Exception):
        env_path.chmod(0o600)

def setup_wizard():
    print("Welcome to AEGIS-COGNITION Setup")
    api_key = get_masked_input("Paste your LLM API Key or Local URL: ")
    provider = detect_provider(api_key)
    if not provider:
        print("Could not auto-detect provider. Please check your key.")
        return
    
    print(f"Detected Provider: {provider}")
    
    trust_level = input("Require physical witness (PROD) or allow degraded hot evidence (DEV)? [PROD/DEV/STAGING]: ").strip().upper()
    if trust_level not in ("PROD", "DEV", "STAGING"):
        trust_level = "PROD"
        print("Defaulting to PROD.")
        
    enable_browser_str = input("Enable Browser Automation? (Y/n): ").strip().lower()
    enable_browser = enable_browser_str in ('', 'y', 'yes')
    
    save_config(provider, api_key, trust_level, enable_browser)
    print("Setup complete. Configuration saved to ~/.aegis/config.yaml and ~/.aegis/.env.")

def apply_config_to_runtime(config: dict | None = None):
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        env_path = get_aegis_dir() / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path)
            
    if config is None:
        config_path = get_aegis_dir() / "config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                config = yaml.safe_load(f)
                
    if config:
        trust_level = config.get("trust_level")
        if trust_level:
            os.environ["AEGIS_TRUST_LEVEL"] = trust_level
            
        provider = config.get("provider")
        if provider:
            os.environ["AEGIS_DEFAULT_PROVIDER"] = provider

def run_agent(task: str):
    apply_config_to_runtime()
    from aegis_adapter import AegisAgent
    agent = AegisAgent()
    print(f"Running AEGIS agent for task: {task}")
    agent.run_sync(task)
    print("Agent Execution Completed.")

def main():
    import argparse
    parser = argparse.ArgumentParser(description="AEGIS-COGNITION DX CLI")
    subparsers = parser.add_subparsers(dest="command")
    
    subparsers.add_parser("init", help="Run the interactive setup wizard")
    
    run_parser = subparsers.add_parser("run", help="Run the AEGIS agent")
    run_parser.add_argument("task", help="The task for the agent to execute")
    
    args = parser.parse_args()
    
    if args.command == "init":
        setup_wizard()
    elif args.command == "run":
        run_agent(args.task)
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
