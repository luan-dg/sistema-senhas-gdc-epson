import os
import time
import socket
import random
import json
import threading
import queue
import sys
import contextlib
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
import win32print

# =========================================================
# CONFIGURAÇÕES
# =========================================================
IP_GDC = "10.1.1.199"
PORTA_GDC = 888
IMPRESSORA = "EPSON TM-T20X Receipt6"
# Em executáveis --onefile, __file__ pode apontar para uma pasta temporária.
# Tentamos gravar ao lado do .exe/script; se a pasta não permitir escrita,
# usamos uma pasta persistente e gravável dentro do perfil do usuário.
if getattr(sys, "frozen", False):
    PASTA_PROGRAMA = os.path.dirname(os.path.abspath(sys.executable))
else:
    PASTA_PROGRAMA = os.path.dirname(os.path.abspath(__file__))


def escolher_pasta_dados():
    candidatos = [
        PASTA_PROGRAMA,
        os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "SistemaSenhasGDC"),
    ]
    ultimo_erro = None
    for pasta in candidatos:
        try:
            os.makedirs(pasta, exist_ok=True)
            teste = os.path.join(pasta, ".teste_escrita.tmp")
            with open(teste, "w", encoding="utf-8") as arquivo:
                arquivo.write("teste")
            os.remove(teste)
            return pasta
        except OSError as erro:
            ultimo_erro = erro
    raise OSError(f"Não foi possível gravar na pasta do programa nem na pasta de dados do usuário. {ultimo_erro}")


PASTA_DADOS = escolher_pasta_dados()
ARQUIVO_SENHAS = os.path.join(PASTA_DADOS, "senhas_gravadas.txt")
ARQUIVO_IMPRESSOS = os.path.join(PASTA_DADOS, "impresso.txt")
ARQUIVO_CONFIG = os.path.join(PASTA_DADOS, "config.json")
TIMEOUT_CONEXAO = 2.0
TIMEOUT_RESPOSTA = 1.5
TEMPO_ENTRE_CADASTROS = 0.1
TEMPO_ENTRE_IMPRESSOES = 0.3
RESPOSTA_SUCESSO_GDC = "{ST&CD}"


def carregar_configuracoes():
    """Carrega config.json ao lado do script/exe; cria-o com padrões se faltar."""
    global IP_GDC, PORTA_GDC, IMPRESSORA
    if not os.path.exists(ARQUIVO_CONFIG):
        salvar_configuracoes()
        return
    try:
        with open(ARQUIVO_CONFIG, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        IP_GDC = str(cfg.get("ip_gdc", IP_GDC)).strip() or IP_GDC
        PORTA_GDC = int(cfg.get("porta_gdc", PORTA_GDC))
        IMPRESSORA = str(cfg.get("impressora", IMPRESSORA)).strip() or IMPRESSORA
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as erro:
        # Mantém os padrões se o arquivo estiver inválido; o usuário pode
        # corrigir os valores pela interface e salvar novamente.
        print(f"Aviso: não foi possível ler config.json: {erro}")


def salvar_configuracoes():
    """Salva as configurações ao lado do .exe, nunca na pasta temporária."""
    with open(ARQUIVO_CONFIG, "w", encoding="utf-8") as f:
        json.dump({"ip_gdc": IP_GDC, "porta_gdc": PORTA_GDC, "impressora": IMPRESSORA}, f, ensure_ascii=False, indent=4)


def preparar_arquivos_dados():
    """Garante que os arquivos de controle existam junto do aplicativo."""
    for caminho in (ARQUIVO_SENHAS, ARQUIVO_IMPRESSOS):
        # Modo append cria o arquivo se ele ainda não existir e preserva seu conteúdo.
        with open(caminho, "a", encoding="utf-8"):
            pass


# =========================================================
# GDC: PROTOCOLO E CONEXÃO PERSISTENTE
# =========================================================

def checksum(dados):
    return sum(dados) & 0xFF


def carregar_senhas(caminho):
    """Lê senhas já registradas no arquivo, ignorando linhas inválidas."""
    if not os.path.exists(caminho):
        return set()

    senhas = set()
    with open(caminho, "r", encoding="utf-8-sig") as arquivo:
        for linha in arquivo:
            senha = linha.strip()
            if senha.isdigit() and len(senha) == 6:
                senhas.add(senha)
    return senhas


def obter_senhas_pendentes():
    """Senhas gravadas no GDC (senhas_gravadas.txt) que ainda não foram impressas (impresso.txt)."""
    registradas = carregar_senhas(ARQUIVO_SENHAS)
    impressas = carregar_senhas(ARQUIVO_IMPRESSOS)
    return sorted(registradas - impressas)


def montar_comando_gdc(senha):
    """Monta o quadro do protocolo GDC para uma senha de seis dígitos."""
    if not senha.isdigit() or len(senha) != 6:
        raise ValueError("A senha deve conter exatamente 6 dígitos.")

    senha_bytes = [int(digito) for digito in senha]

    dados = bytes([
        ord("S"),
        0x0F, 0xEE, 0x00, 0x00,
        *senha_bytes,
        0xDE, 0xDE,
        7, 0,       # Duração: 7 minutos
        0, 1,       # Ducha 0, validade 1
        0x0F
    ])

    ck = checksum(dados + b"&")
    return b"{" + dados + b"&" + f"{ck:02X}".encode("ascii") + b"}"


class ConexaoGDC:
    """
    Mantém uma conexão TCP aberta durante o lote.

    Se ocorrer timeout ou erro de socket, fecha a conexão para não deixar
    uma resposta atrasada ser confundida com a resposta da próxima senha.
    A senha que teve timeout NÃO é reenviada automaticamente, pois o GDC
    pode tê-la gravado mesmo sem a confirmação chegar ao computador.
    """

    def __init__(self):
        self.socket = None

    def conectar(self):
        self.fechar()
        conexao = socket.create_connection(
            (IP_GDC, PORTA_GDC),
            timeout=TIMEOUT_CONEXAO
        )
        conexao.settimeout(TIMEOUT_RESPOSTA)
        self.socket = conexao

    def fechar(self):
        if self.socket is not None:
            try:
                self.socket.close()
            except OSError:
                pass
            finally:
                self.socket = None

    def gravar(self, senha):
        comando = montar_comando_gdc(senha)

        try:
            # Conecta somente na primeira senha ou se for necessário recuperar
            # uma conexão que foi encerrada/descartada após um erro.
            if self.socket is None:
                self.conectar()

            self.socket.sendall(comando)

            # TCP é um fluxo: a resposta pode chegar em mais de um recv().
            # Lemos até o terminador do quadro "}".
            resposta = bytearray()
            while b"}" not in resposta:
                parte = self.socket.recv(1024)

                if not parte:
                    raise ConnectionError("O GDC encerrou a conexão.")

                resposta.extend(parte)

                if len(resposta) > 1024:
                    raise ValueError("Resposta do GDC muito longa.")

            # Se o equipamento enviar bytes após o primeiro quadro, não
            # descartamos silenciosamente; o protocolo esperado é um quadro
            # de resposta por comando.
            quadro = bytes(resposta[:resposta.index(b"}") + 1])
            return quadro.decode("ascii", errors="replace").strip()

        except (socket.timeout, OSError, ValueError):
            # Descarta a conexão em caso de falha para evitar dessincronização
            # entre respostas. A próxima senha tentará abrir outra conexão.
            self.fechar()
            raise


# =========================================================
# CADASTRO DE SENHAS NO GDC
# =========================================================

def cadastrar_senhas(quantidade):
    """
    Gera senhas únicas e as envia ao GDC.
    Salva somente respostas confirmadas e retorna (confirmadas, falhas).
    """
    registradas = carregar_senhas(ARQUIVO_SENHAS)
    impressas = carregar_senhas(ARQUIVO_IMPRESSOS)
    bloqueadas = registradas | impressas

    disponiveis = 1_000_000 - len(bloqueadas)
    if quantidade > disponiveis:
        raise ValueError(
            "A quantidade solicitada excede as senhas únicas disponíveis."
        )

    geradas = set()
    while len(geradas) < quantidade:
        senha = f"{random.randint(0, 999999):06d}"
        if senha not in bloqueadas:
            geradas.add(senha)

    # Ordenação não é necessária para o GDC, mas torna a execução previsível
    # dentro do lote e facilita conferir os registros.
    senhas_geradas = list(geradas)
    confirmadas = []
    falhas = []

    print("\nIniciando cadastro no GDC...")
    print(f"Total solicitado: {quantidade}")
    print("Modo: conexão persistente, com reconexão se houver erro.\n")

    inicio_lote = time.perf_counter()
    conexao_gdc = ConexaoGDC()

    try:
        # Mantém o arquivo aberto durante todo o lote.
        with open(ARQUIVO_SENHAS, "a", encoding="utf-8") as arquivo:
            for indice, senha in enumerate(senhas_geradas, start=1):
                inicio_senha = time.perf_counter()
                print(
                    f"[{indice}/{quantidade}] Enviando senha {senha}...",
                    end=" ",
                    flush=True
                )

                try:
                    resposta = conexao_gdc.gravar(senha)

                    if resposta == RESPOSTA_SUCESSO_GDC:
                        arquivo.write(senha + "\n")
                        # Mantém confirmação gravada no arquivo a cada senha,
                        # para reduzir o risco de perder registros em interrupção.
                        arquivo.flush()
                        confirmadas.append(senha)
                        duracao = time.perf_counter() - inicio_senha
                        print(f"✓ CONFIRMADA ({duracao:.2f} s)")
                    else:
                        falhas.append(
                            (senha, f"Resposta inesperada: {resposta or 'vazia'}")
                        )
                        print(
                            f"⚠ RESPOSTA NÃO CONFIRMADA "
                            f"({resposta or 'vazia'})"
                        )
                        # Uma resposta inesperada pode indicar desalinhamento
                        # do protocolo; abre uma conexão nova para a próxima.
                        conexao_gdc.fechar()

                except socket.timeout:
                    falhas.append((senha, "TIMEOUT aguardando resposta do GDC"))
                    print("⚠ TIMEOUT")
                except OSError as erro:
                    falhas.append((senha, f"Erro de conexão/socket: {erro}"))
                    print(f"✗ ERRO DE CONEXÃO ({erro})")
                except Exception as erro:
                    falhas.append((senha, f"Erro inesperado: {erro}"))
                    print(f"✗ ERRO ({erro})")

                if indice < quantidade and TEMPO_ENTRE_CADASTROS > 0:
                    time.sleep(TEMPO_ENTRE_CADASTROS)

    finally:
        conexao_gdc.fechar()

    tempo_total = time.perf_counter() - inicio_lote
    print("\nCadastro finalizado.")
    print(f"Confirmadas pelo GDC: {len(confirmadas)}")
    print(f"Não confirmadas: {len(falhas)}")
    print(f"Tempo total do cadastro: {tempo_total:.2f} segundos")
    if quantidade:
        print(f"Média por senha solicitada: {tempo_total / quantidade:.2f} segundos")

    return confirmadas, falhas


# =========================================================
# EPSON: IMPRESSÃO
# =========================================================

def imprimir(codigo):
    """Imprime uma senha na Epson TM-T20X usando comandos ESC/POS."""
    h_printer = win32print.OpenPrinter(IMPRESSORA)
    documento_iniciado = False
    pagina_iniciada = False

    try:
        win32print.StartDocPrinter(h_printer, 1, ("Senha", None, "RAW"))
        documento_iniciado = True

        win32print.StartPagePrinter(h_printer)
        pagina_iniciada = True

        dados = bytearray()

        # Inicializa a impressora e centraliza.
        dados += b"\x1b\x40"
        dados += b"\x1b\x61\x01"

        # Título.
        dados += b"\x1d\x21\x11"
        dados += "Banho de 7 minutos\n\n".encode("cp850")

        # Número da senha em fonte grande.
        dados += b"\x1d\x21\x77"
        dados += codigo.encode("ascii")

        # Fonte normal, avanço e corte.
        dados += b"\x1d\x21\x00"
        dados += b"\n\n\n\n\n\n"
        dados += b"\x1d\x56\x00"

        win32print.WritePrinter(h_printer, bytes(dados))

    finally:
        if pagina_iniciada:
            try:
                win32print.EndPagePrinter(h_printer)
            except Exception:
                pass
        if documento_iniciado:
            try:
                win32print.EndDocPrinter(h_printer)
            except Exception:
                pass
        win32print.ClosePrinter(h_printer)


def imprimir_senhas(senhas):
    """Imprime somente senhas confirmadas que ainda não constam como impressas."""
    impressas = carregar_senhas(ARQUIVO_IMPRESSOS)
    fila = [senha for senha in senhas if senha not in impressas]

    if not fila:
        print("\nNão há senhas confirmadas novas para imprimir.")
        return

    print("\nIniciando impressão na Epson...")
    print(f"Total na fila: {len(fila)}\n")

    sucesso = 0
    erros = 0

    with open(ARQUIVO_IMPRESSOS, "a", encoding="utf-8") as arquivo:
        for indice, senha in enumerate(fila, start=1):
            print(
                f"[{indice}/{len(fila)}] Imprimindo senha {senha}...",
                end=" ",
                flush=True
            )

            try:
                imprimir(senha)
                arquivo.write(senha + "\n")
                arquivo.flush()
                impressas.add(senha)
                sucesso += 1
                print("✓ ENVIADA PARA IMPRESSORA")

            except Exception as erro:
                erros += 1
                print(f"✗ ERRO: {erro}")

            if indice < len(fila):
                time.sleep(TEMPO_ENTRE_IMPRESSOES)

    print("\nImpressão finalizada.")
    print(f"Enviadas para a impressora: {sucesso}")
    print(f"Com erro: {erros}")



class FilaSaida:
    """Redireciona a saída estilo CMD para a caixa de texto da interface."""
    def __init__(self, fila):
        self.fila = fila
    def write(self, texto):
        if texto:
            self.fila.put(("LOG", texto))
    def flush(self):
        pass


class Sistema(ctk.CTk):
    def __init__(self):
        super().__init__()
        carregar_configuracoes()
        self.title("Sistema de Senhas | GDC + Epson")
        self.geometry("900x700")
        self.minsize(760, 580)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.fila = queue.Queue()
        self.ocupado = False
        self.confirmadas_pendentes = []
        self.falhas_pendentes = []
        self._montar_interface()
        self.after(100, self._processar_fila)
        self.log("Sistema iniciado.")
        self.log(f"Pasta dos arquivos: {PASTA_DADOS}")
        self.log(f"Configuração: {ARQUIVO_CONFIG}")
        self.log(f"Controle de senhas: {ARQUIVO_SENHAS}")
        self.log(f"Controle de impressões: {ARQUIVO_IMPRESSOS}")
        self.log(f"GDC atual: {IP_GDC}:{PORTA_GDC}")
        self.log(f"Impressora atual: {IMPRESSORA}")
        self.log("Configure os equipamentos acima e informe a quantidade de senhas.\n")

    def _montar_interface(self):
        # Abas principais: operação e informações do aplicativo.
        self.abas = ctk.CTkTabview(self, corner_radius=10)
        self.abas.pack(fill="both", expand=True, padx=10, pady=10)
        aba_sistema = self.abas.add("Sistema")
        aba_sobre = self.abas.add("Sobre")

        topo = ctk.CTkFrame(aba_sistema, corner_radius=10)
        topo.pack(fill="x", padx=14, pady=(14, 8))
        ctk.CTkLabel(topo, text="SISTEMA DE IMPRESSÃO E CADASTRO", font=ctk.CTkFont(size=19, weight="bold")).pack(anchor="w", padx=16, pady=(12, 8))

        linha1 = ctk.CTkFrame(topo, fg_color="transparent")
        linha1.pack(fill="x", padx=14, pady=5)
        ctk.CTkLabel(linha1, text="Impressora", width=95, anchor="w").pack(side="left")
        try:
            impressoras = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS)]
        except Exception:
            impressoras = []
        if IMPRESSORA and IMPRESSORA not in impressoras:
            impressoras.insert(0, IMPRESSORA)
        self.impressora_var = tk.StringVar(value=IMPRESSORA)
        self.combo_impressora = ctk.CTkComboBox(linha1, values=impressoras or [IMPRESSORA], variable=self.impressora_var, width=440)
        self.combo_impressora.pack(side="left", fill="x", expand=True, padx=(0, 12))
        ctk.CTkLabel(linha1, text="Quantidade", width=80, anchor="w").pack(side="left")
        self.quantidade_var = tk.StringVar(value="10")
        ctk.CTkEntry(linha1, textvariable=self.quantidade_var, width=90).pack(side="left")

        linha2 = ctk.CTkFrame(topo, fg_color="transparent")
        linha2.pack(fill="x", padx=14, pady=5)
        ctk.CTkLabel(linha2, text="IP do GDC", width=95, anchor="w").pack(side="left")
        self.ip_var = tk.StringVar(value=IP_GDC)
        ctk.CTkEntry(linha2, textvariable=self.ip_var, width=220).pack(side="left", padx=(0, 14))
        ctk.CTkLabel(linha2, text="Porta", width=45, anchor="w").pack(side="left")
        self.porta_var = tk.StringVar(value=str(PORTA_GDC))
        ctk.CTkEntry(linha2, textvariable=self.porta_var, width=90).pack(side="left", padx=(0, 14))
        self.btn_salvar = ctk.CTkButton(linha2, text="Salvar configuração", command=self.salvar_config, width=150)
        self.btn_salvar.pack(side="left", padx=(0, 8))
        self.btn_testar = ctk.CTkButton(linha2, text="Testar GDC", command=self.testar_conexao, width=110, fg_color="#4b5563", hover_color="#374151")
        self.btn_testar.pack(side="left")

        barra = ctk.CTkFrame(topo, fg_color="transparent")
        barra.pack(fill="x", padx=14, pady=(8, 13))
        self.btn_iniciar = ctk.CTkButton(barra, text="Cadastrar senhas", command=self.iniciar_cadastro, height=36)
        self.btn_iniciar.pack(side="left")
        self.btn_pendentes = ctk.CTkButton(barra, text="Imprimir pendentes", command=self.imprimir_pendentes, height=36, fg_color="#15803d", hover_color="#166534")
        self.btn_pendentes.pack(side="left", padx=(8, 0))
        self.btn_limpar = ctk.CTkButton(barra, text="Limpar saída", command=self.limpar_saida, height=36, fg_color="#4b5563", hover_color="#374151")
        self.btn_limpar.pack(side="left", padx=8)
        self._atualizar_botoes()
        self.status = ctk.CTkLabel(barra, text="Pronto", anchor="e")
        self.status.pack(side="right", fill="x", expand=True)

        saida_frame = ctk.CTkFrame(aba_sistema, corner_radius=10)
        saida_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        cab = ctk.CTkFrame(saida_frame, fg_color="transparent")
        cab.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(cab, text="SAÍDA DO SISTEMA", font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkLabel(cab, text="Console de execução", text_color="#9ca3af").pack(side="right")
        self.saida = ctk.CTkTextbox(saida_frame, wrap="word", font=ctk.CTkFont(family="Consolas", size=12), fg_color="#090d12", text_color="#d1fae5")
        self.saida.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        self.saida.configure(state="disabled")

        # Aba Sobre: informações e créditos do programa.
        sobre_card = ctk.CTkFrame(aba_sobre, corner_radius=12)
        sobre_card.pack(fill="both", expand=True, padx=18, pady=18)
        ctk.CTkLabel(
            sobre_card,
            text="Sistema de Senhas",
            font=ctk.CTkFont(size=24, weight="bold")
        ).pack(anchor="w", padx=24, pady=(24, 4))
       

        ctk.CTkLabel(
            sobre_card,
            text="SOBRE O APLICATIVO",
            font=ctk.CTkFont(size=13, weight="bold")
        ).pack(anchor="w", padx=24, pady=(0, 6))
        ctk.CTkLabel(
            sobre_card,
            text=(
                "Aplicativo de uso interno do posto para gerar e cadastrar senhas "
                "no equipamento GDC pela rede e imprimir as senhas confirmadas "
                "em uma impressora térmica Epson compatível.\n\n"
                "As configurações de IP, porta e impressora ficam no arquivo "
                "config.json, separado do programa.\n\n"
                "Lógica aprimorada para o cadastro de senhas no GDC, sendo mais "
                "eficiente que o software original, aproximadamente 95% mais rápido "
                "na gravação de senhas."
            ),
            justify="left",
            anchor="w",
            wraplength=690
        ).pack(fill="x", padx=24, pady=(0, 18))

        ctk.CTkLabel(
            sobre_card,
            text="TECNOLOGIAS",
            font=ctk.CTkFont(size=13, weight="bold")
        ).pack(anchor="w", padx=24, pady=(0, 6))
        ctk.CTkLabel(
            sobre_card,
            text="Python • CustomTkinter • TCP/IP • pywin32 • ESC/POS",
            justify="left",
            anchor="w"
        ).pack(fill="x", padx=24, pady=(0, 18))

        ctk.CTkLabel(
            sobre_card,
            text="CRÉDITOS",
            font=ctk.CTkFont(size=13, weight="bold")
        ).pack(anchor="w", padx=24, pady=(0, 6))
        ctk.CTkLabel(
            sobre_card,
            text=(
                "Desenvolvimento e manutenção: Luan Delaflora Guth\n"
                "Finalidade: automação e aprimoramento do cadastro e impressão de senhas usando o GDC."
            ),
            justify="left",
            anchor="w"
        ).pack(fill="x", padx=24, pady=(0, 18))

        ctk.CTkLabel(
            sobre_card,
            text="Versão 3.0  •  © 2026",
            text_color="#9ca3af"
        ).pack(anchor="w", padx=24, pady=(0, 24))

    def log(self, texto):
        self.saida.configure(state="normal")
        self.saida.insert("end", texto)
        self.saida.see("end")
        self.saida.configure(state="disabled")

    def limpar_saida(self):
        self.saida.configure(state="normal")
        self.saida.delete("1.0", "end")
        self.saida.configure(state="disabled")

    def _validar_config(self):
        global IP_GDC, PORTA_GDC, IMPRESSORA
        ip = self.ip_var.get().strip()
        impressora = self.impressora_var.get().strip()
        try:
            porta = int(self.porta_var.get().strip())
            if not 1 <= porta <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("Porta inválida", "Informe uma porta entre 1 e 65535.", parent=self)
            return False
        if not ip or not impressora:
            messagebox.showerror("Campos obrigatórios", "Informe o IP e selecione uma impressora.", parent=self)
            return False
        IP_GDC, PORTA_GDC, IMPRESSORA = ip, porta, impressora
        return True

    def salvar_config(self):
        if not self._validar_config():
            return
        try:
            salvar_configuracoes()
            self.log(f"Configuração salva: GDC {IP_GDC}:{PORTA_GDC} | Impressora: {IMPRESSORA}\n")
            self.status.configure(text="Configuração salva")
        except OSError as erro:
            messagebox.showerror("Erro ao salvar", str(erro), parent=self)

    def testar_conexao(self):
        if not self._validar_config():
            return
        try:
            salvar_configuracoes()
        except OSError as erro:
            messagebox.showerror("Erro ao salvar", str(erro), parent=self)
            return
        self.log(f"Testando conexão com {IP_GDC}:{PORTA_GDC}...\n")
        self.status.configure(text="Testando conexão...")
        self.btn_testar.configure(state="disabled")
        threading.Thread(target=self._teste_worker, daemon=True).start()

    def _teste_worker(self):
        try:
            with socket.create_connection((IP_GDC, PORTA_GDC), timeout=2):
                self.fila.put(("TESTE", True, "Conexão TCP estabelecida com sucesso."))
        except OSError as erro:
            self.fila.put(("TESTE", False, str(erro)))

    def _atualizar_botoes(self):
        """Habilita/desabilita os botões de ação e mostra a quantidade de senhas pendentes."""
        estado = "disabled" if self.ocupado else "normal"
        self.btn_iniciar.configure(state=estado)
        try:
            qtd = len(obter_senhas_pendentes())
        except OSError:
            qtd = 0
        texto = f"Imprimir pendentes ({qtd})" if qtd else "Imprimir pendentes"
        self.btn_pendentes.configure(state=estado, text=texto)

    def imprimir_pendentes(self):
        """Imprime as senhas gravadas no GDC que ainda não constam em impresso.txt."""
        if self.ocupado:
            return
        if not self._validar_config():
            return
        try:
            pendentes = obter_senhas_pendentes()
        except OSError as erro:
            messagebox.showerror("Erro ao ler arquivos", str(erro), parent=self)
            return
        if not pendentes:
            self.log("Não há senhas pendentes de impressão.\n")
            messagebox.showinfo("Imprimir pendentes", "Não há senhas gravadas pendentes de impressão.", parent=self)
            return
        if not messagebox.askyesno("Imprimir pendentes", f"Existem {len(pendentes)} senha(s) gravada(s) que ainda não foram impressas.\n\nImprimir agora em \"{IMPRESSORA}\"?", parent=self):
            self.log("Impressão de pendentes cancelada pelo usuário.\n")
            return
        try:
            salvar_configuracoes()
        except OSError as erro:
            messagebox.showerror("Erro ao salvar", str(erro), parent=self)
            return
        self.ocupado = True
        self._atualizar_botoes()
        self.status.configure(text="Imprimindo pendentes...")
        self.log("\n" + "=" * 55 + "\n")
        self.log(f"Imprimindo {len(pendentes)} senha(s) pendente(s)...\n")
        threading.Thread(target=self._impressao_worker, args=(pendentes,), daemon=True).start()

    def iniciar_cadastro(self):
        if self.ocupado:
            return
        if not self._validar_config():
            return
        try:
            quantidade = int(self.quantidade_var.get().strip())
            if quantidade < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Quantidade inválida", "Digite uma quantidade inteira maior que zero.", parent=self)
            return
        if not messagebox.askyesno("Confirmar cadastro", f"Cadastrar {quantidade} senhas no GDC {self.ip_var.get()}:{self.porta_var.get()}?\n\nAs senhas confirmadas poderão ser impressas após o cadastro.", parent=self):
            self.log("Operação cancelada pelo usuário.\n")
            return
        try:
            salvar_configuracoes()
        except OSError as erro:
            messagebox.showerror("Erro ao salvar", str(erro), parent=self)
            return
        self.ocupado = True
        self._atualizar_botoes()
        self.status.configure(text="Cadastrando senhas...")
        self.log("\n" + "=" * 55 + "\n")
        self.log(f"Iniciando lote de {quantidade} senhas.\n")
        threading.Thread(target=self._cadastro_worker, args=(quantidade,), daemon=True).start()

    def _cadastro_worker(self, quantidade):
        try:
            saida = FilaSaida(self.fila)
            with contextlib.redirect_stdout(saida):
                confirmadas, falhas = cadastrar_senhas(quantidade)
            self.fila.put(("CADASTRO_OK", confirmadas, falhas))
        except Exception as erro:
            self.fila.put(("ERRO", f"Erro ao iniciar/finalizar cadastro: {erro}"))

    def _impressao_worker(self, confirmadas):
        try:
            saida = FilaSaida(self.fila)
            with contextlib.redirect_stdout(saida):
                imprimir_senhas(confirmadas)
            self.fila.put(("IMPRESSAO_OK",))
        except Exception as erro:
            self.fila.put(("ERRO", f"Erro durante a impressão: {erro}"))

    def _processar_fila(self):
        try:
            while True:
                evento = self.fila.get_nowait()
                tipo = evento[0]
                if tipo == "LOG":
                    self.log(evento[1])
                elif tipo == "TESTE":
                    self.btn_testar.configure(state="normal")
                    if evento[1]:
                        self.log("[OK] " + evento[2] + "\n")
                        self.status.configure(text="GDC conectado")
                        messagebox.showinfo("Teste do GDC", evento[2], parent=self)
                    else:
                        self.log("[ERRO] Falha de conexão: " + evento[2] + "\n")
                        self.status.configure(text="Falha na conexão")
                        messagebox.showerror("Teste do GDC", evento[2], parent=self)
                elif tipo == "CADASTRO_OK":
                    self.confirmadas_pendentes, self.falhas_pendentes = evento[1], evento[2]
                    self.log(f"\nLote finalizado. Senhas confirmadas: {len(evento[1])}; falhas: {len(evento[2])}.\n")
                    self.ocupado = False
                    self._atualizar_botoes()
                    self.status.configure(text="Cadastro finalizado")
                    if evento[1]:
                        if messagebox.askyesno("Imprimir senhas?", f"O GDC confirmou {len(evento[1])} senha(s).\n\nDeseja imprimir agora somente as confirmadas?", parent=self):
                            self.ocupado = True
                            self._atualizar_botoes()
                            self.status.configure(text="Imprimindo...")
                            self.log("\nIniciando impressão das senhas confirmadas...\n")
                            threading.Thread(target=self._impressao_worker, args=(evento[1],), daemon=True).start()
                        else:
                            self.log("Impressão não realizada. As senhas continuam pendentes; use o botão Imprimir pendentes quando quiser.\n")
                    else:
                        self.log("Nenhuma senha confirmada; nada será impresso.\n")
                elif tipo == "IMPRESSAO_OK":
                    self.ocupado = False
                    self._atualizar_botoes()
                    self.status.configure(text="Processo concluído")
                    self.log("Processo concluído.\n")
                elif tipo == "ERRO":
                    self.ocupado = False
                    self._atualizar_botoes()
                    self.status.configure(text="Erro")
                    self.log("[ERRO] " + evento[1] + "\n")
                    messagebox.showerror("Erro no sistema", evento[1], parent=self)
        except queue.Empty:
            pass
        self.after(100, self._processar_fila)


if __name__ == "__main__":
    try:
        preparar_arquivos_dados()
        carregar_configuracoes()
        app = Sistema()
        app.mainloop()
    except OSError as erro:
        # Se o programa estiver em uma pasta sem permissão de escrita, deixe
        # claro o motivo em vez de falhar silenciosamente no executável sem console.
        try:
            messagebox.showerror(
                "Erro de arquivos",
                "Não foi possível criar/abrir os arquivos de configuração e controle.\n\n"
                f"Pasta do programa: {PASTA_PROGRAMA}\nErro: {erro}\n\n"
                "Tente executar o programa a partir de uma pasta gravável, por exemplo C:\\SistemaSenhas."
            )
        except Exception:
            pass
