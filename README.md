# 🖨️ Sistema de Senhas GDC + Epson

Aplicação desktop desenvolvida em Python para automatizar o cadastro de senhas de banho em um sistema GDC Manager e realizar a impressão dos tickets em uma impressora térmica Epson TM-T20X.

O projeto busca agilizar o processo de cadastro, reduzir tarefas manuais e facilitar o acompanhamento das senhas processadas.

## ✨ Funcionalidades

- **Interface gráfica:** aplicação construída com CustomTkinter.
- **Comunicação TCP/IP:** envio de senhas ao GDC Manager pela rede local.
- **Conexão persistente:** reutilização da conexão de rede durante a execução para reduzir o tempo de processamento.
- **Impressão térmica:** envio direto de comandos ESC/POS para a impressora Epson.
- **Controle de senhas:** registro das senhas processadas em arquivos de texto.
- **Configurações:** armazenamento do endereço IP, porta de comunicação e nome da impressora em `config.json`.
- **Acompanhamento da execução:** exibição de mensagens e informações sobre o processamento.
- **Executável para Windows:** geração de um arquivo `.exe` utilizando PyInstaller.

## 🛠️ Tecnologias utilizadas

- Python
- CustomTkinter
- Sockets TCP/IP
- Win32 Print API (`pywin32`)
- Protocolo ESC/POS
- PyInstaller

## 📋 Requisitos

- Windows
- Python instalado para executar o código-fonte
- GDC Manager acessível pela rede local
- Impressora térmica Epson compatível
- Dependências Python instaladas

## ⚙️ Configuração

As configurações são armazenadas no arquivo `config.json`, incluindo:

- Endereço IP do GDC.
- Porta de comunicação TCP/IP.
- Nome da impressora instalada no Windows.

Confira se o endereço IP, a porta e o nome da impressora correspondem ao ambiente em que o programa será executado.

## 🚀 Como executar

### Executar pelo Python

1. Clone ou baixe este repositório.
2. Instale as dependências necessárias.
3. Configure o endereço IP, a porta e a impressora.
4. Execute o arquivo principal do sistema.

### Gerar o executável

O projeto inclui um script `.bat` para automatizar a compilação com PyInstaller.

1. Deixe o código Python, o script `.bat` e o ícone `.ico` na estrutura esperada pelo compilador.
2. Execute o arquivo `.bat`.
3. Aguarde a conclusão da compilação.
4. Localize o executável gerado na pasta `dist`.

## 📁 Arquivos de controle

- `config.json`: configurações do sistema.
- `senhas_gravadas.txt`: registro das senhas gravadas no GDC.
- `impresso.txt`: controle das senhas impressas.

Os arquivos de controle são utilizados para acompanhar o processamento e evitar a perda de informações entre as execuções, conforme a lógica implementada no programa.

## 🎯 Objetivo do projeto

Desenvolver uma solução prática para automatizar o fluxo de cadastro e impressão de senhas de banho, integrando um sistema GDC a uma impressora térmica por meio de comunicação em rede.

O projeto também serve como aplicação prática de Python, interfaces gráficas, sockets, automação de processos e integração com dispositivos de impressão.

## 📌 Observações

- O funcionamento depende da disponibilidade do GDC e da conexão com a rede local.
- O computador precisa reconhecer a impressora e permitir o envio de trabalhos de impressão.
- Faça cópias de segurança dos arquivos de controle antes de alterações ou manutenção.
- Teste a comunicação e a impressão no ambiente de destino antes de utilizar o sistema em produção.

## 👨‍💻 Desenvolvimento

Projeto desenvolvido em Python com foco em automação, desempenho e integração entre software, rede local e impressora térmica.
