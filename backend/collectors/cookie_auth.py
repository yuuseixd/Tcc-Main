"""
Módulo de extração de cookies do Twitter via navegador real.
Usa undetected-chromedriver para evitar detecção anti-bot do Cloudflare/Twitter.
Fallback: copiar cookies do Chrome real ou colar manualmente.

Adaptado do projeto twitter-telegram-monitor para o SentCrypto.
"""

import json
import time
import shutil
import tempfile
import os
from pathlib import Path

COOKIES_PATH = Path(__file__).parent.parent / "x_cookies.json"


def extrair_cookies_do_navegador() -> bool:
    """
    Tenta extrair cookies do Twitter de várias formas:
    1. undetected-chromedriver (navegador anti-detecção)
    2. Chrome com perfil real do usuário (cópia temporária)
    3. Entrada manual de cookies como último recurso

    Returns:
        True se os cookies foram extraídos com sucesso.
    """
    print("\n" + "=" * 55)
    print("  LOGIN NO TWITTER")
    print("=" * 55)

    # Tenta método 1: undetected-chromedriver
    print("\n  [1/3] Tentando login com navegador stealth...")
    if _login_undetected():
        return True

    # Tenta método 2: Chrome com perfil real (pode já estar logado)
    print("\n  [2/3] Tentando usar seu perfil real do Chrome...")
    if _login_perfil_real():
        return True

    # Método 3: entrada manual de cookies
    print("\n  [3/3] Método manual: cole os cookies do navegador.")
    return _login_manual_cookies()


def _login_undetected() -> bool:
    """Login usando undetected-chromedriver (evita Cloudflare)."""
    try:
        import undetected_chromedriver as uc
    except ImportError:
        print("      undetected-chromedriver não instalado, pulando...")
        return False

    driver = None
    try:
        options = uc.ChromeOptions()
        options.add_argument("--window-size=1280,900")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")

        print("      Abrindo Chrome (modo stealth)...")
        print("      -> Faça login normalmente no Twitter.")
        print("      -> Timeout: 3 minutos.\n")

        driver = uc.Chrome(options=options, version_main=None)
        driver.get("https://x.com/i/flow/login")

        return _aguardar_login(driver)

    except Exception as e:
        print(f"      Falha: {e}")
        return False
    finally:
        _fechar_driver(driver)


def _login_perfil_real() -> bool:
    """
    Abre Chrome usando uma CÓPIA do perfil real do usuário.
    Se o usuário já está logado no Chrome, os cookies já estarão lá.
    """
    chrome_user_data = (
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google"
        / "Chrome"
        / "User Data"
    )

    if not chrome_user_data.exists():
        print("      Perfil do Chrome não encontrado, pulando...")
        return False

    temp_dir = Path(tempfile.mkdtemp(prefix="sentcrypto_"))

    driver = None
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options

        default_profile = chrome_user_data / "Default"
        if not default_profile.exists():
            print("      Perfil Default não encontrado, pulando...")
            return False

        print("      Copiando perfil do Chrome (pode demorar)...")
        temp_profile = temp_dir / "Default"
        temp_profile.mkdir(parents=True, exist_ok=True)

        for arquivo in [
            "Cookies",
            "Cookies-journal",
            "Login Data",
            "Preferences",
            "Secure Preferences",
        ]:
            src = default_profile / arquivo
            if src.exists():
                shutil.copy2(src, temp_profile / arquivo)

        local_state = chrome_user_data / "Local State"
        if local_state.exists():
            shutil.copy2(local_state, temp_dir / "Local State")

        options = Options()
        options.add_argument(f"--user-data-dir={temp_dir}")
        options.add_argument("--profile-directory=Default")
        options.add_argument("--window-size=1280,900")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])

        print("      Abrindo Chrome com seu perfil...")
        driver = webdriver.Chrome(options=options)
        driver.get("https://x.com/home")
        time.sleep(5)

        cookies = driver.get_cookies()
        cookie_names = {c["name"] for c in cookies}
        if "auth_token" in cookie_names and "ct0" in cookie_names:
            print("      Já logado via perfil do Chrome!")
            return _salvar_cookies(driver)

        print("      Não está logado. Faça login na janela do Chrome.")
        print("      -> Timeout: 3 minutos.\n")
        driver.get("https://x.com/i/flow/login")
        return _aguardar_login(driver)

    except Exception as e:
        print(f"      Falha: {e}")
        return False
    finally:
        _fechar_driver(driver)
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def _login_manual_cookies() -> bool:
    """
    Último recurso: o usuário cola os cookies manualmente.
    """
    print()
    print("  " + "=" * 50)
    print("  METODO MANUAL: Extrair cookies do navegador")
    print("  " + "=" * 50)
    print()
    print("  Siga estes passos no seu Chrome/Edge/Firefox:")
    print()
    print("  1. Abra https://x.com e faça login normalmente")
    print("  2. Pressione F12 (DevTools)")
    print("  3. Vá na aba 'Application' (Chrome) ou 'Storage' (Firefox)")
    print("  4. No menu lateral: Cookies -> https://x.com")
    print("  5. Encontre e copie o VALOR destes 2 cookies:")
    print()
    print("     - auth_token")
    print("     - ct0")
    print()

    auth_token = input("  Cole o valor de auth_token: ").strip()
    if not auth_token:
        print("  auth_token é obrigatório.")
        return False

    ct0 = input("  Cole o valor de ct0: ").strip()
    if not ct0:
        print("  ct0 é obrigatório.")
        return False

    cookies_dict = {
        "auth_token": auth_token,
        "ct0": ct0,
    }

    with open(COOKIES_PATH, "w", encoding="utf-8") as f:
        json.dump(cookies_dict, f, indent=2, ensure_ascii=False)

    print(f"\n  Cookies salvos com sucesso!")
    return True


def _aguardar_login(driver, timeout: int = 180) -> bool:
    """Aguarda o usuário fazer login e detecta cookies de autenticação."""
    inicio = time.time()

    while time.time() - inicio < timeout:
        time.sleep(1)
        try:
            cookies = driver.get_cookies()
            cookie_names = {c["name"] for c in cookies}

            if "auth_token" in cookie_names and "ct0" in cookie_names:
                print("\n  Login detectado! Salvando cookies...")
                return _salvar_cookies(driver)

            if "/home" in driver.current_url:
                time.sleep(1)
                cookies = driver.get_cookies()
                cookie_names = {c["name"] for c in cookies}
                if "auth_token" in cookie_names:
                    print("\n  Login detectado! Salvando cookies...")
                    return _salvar_cookies(driver)

        except Exception:
            pass

    print("\n  Timeout: login não detectado em 3 minutos.")
    return False


def _salvar_cookies(driver) -> bool:
    """Extrai e salva cookies do driver em arquivo JSON."""
    try:
        cookies_selenium = driver.get_cookies()
        cookies_dict = {}
        for cookie in cookies_selenium:
            cookies_dict[cookie["name"]] = cookie["value"]

        with open(COOKIES_PATH, "w", encoding="utf-8") as f:
            json.dump(cookies_dict, f, indent=2, ensure_ascii=False)

        tem_auth = "auth_token" in cookies_dict
        tem_ct0 = "ct0" in cookies_dict
        print(f"  {len(cookies_dict)} cookies salvos")
        print(
            f"  auth_token: {'OK' if tem_auth else 'FALHA'}  "
            f"ct0: {'OK' if tem_ct0 else 'FALHA'}"
        )
        return tem_auth and tem_ct0
    except Exception as e:
        print(f"  [ERRO] Falha ao salvar cookies: {e}")
        return False


def _fechar_driver(driver):
    """Fecha o driver de forma segura."""
    if driver:
        try:
            driver.quit()
        except Exception:
            pass


def cookies_validos() -> bool:
    """Verifica se existe um arquivo de cookies com tokens essenciais."""
    if not COOKIES_PATH.exists():
        return False

    try:
        with open(COOKIES_PATH, "r", encoding="utf-8") as f:
            cookies = json.load(f)

        return "auth_token" in cookies and "ct0" in cookies
    except (json.JSONDecodeError, IOError):
        return False


def deletar_cookies():
    """Remove cookies salvos (força re-login)."""
    if COOKIES_PATH.exists():
        COOKIES_PATH.unlink()
        print("[OK] Cookies removidos. Será necessário fazer login novamente.")
    else:
        print("[!] Nenhum cookie salvo encontrado.")
