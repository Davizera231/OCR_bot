import discord
import mysql.connector
from io import BytesIO
from PIL import Image

from src.ocr_processor import extrair_quantidade_sucata, TEXTO_ALVO 
import re
import time
from typing import Optional, Dict, List, Tuple
import asyncio 
from datetime import datetime, timedelta, timezone 
import json 
import pandas as pd 
from discord import Attachment 

DISCORD_TOKEN = 'TOKEN-DO-DISCORD' 
CANAL_ID_DE_LOGS = 0000000000000000  
ITEM_DE_LOG_ALVO = 'sucalixo'
INTERVALO_AUDITORIA_MINUTOS = 5 
FUZZY_MATCH_MARGEM = 5 
BUSCA_IMEDIATA_LOOKBACK_MINUTES = 5 
CATEGORIA_ID_ORCAMENTO = 0000000000000000 


QUANTIDADE_MINIMA_DEPOSITO = 200 
QUANTIDADE_MINIMA_AUDITORIA = 50 

DB_CONFIG = {
    'user': 'bot_user',
    'password': '',
    'host': 'localhost', 
    'database': 'user_sucata',
}


def conectar_db():
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error as err:
        print(f"Erro ao conectar ao MySQL: {err}")
        return None



def vincular_discord_license(discord_id, license_id):
    db = conectar_db()
    if not db: return False
    try:
        cursor = db.cursor()
        sql = "REPLACE INTO discord_fivem_map (discord_id, license_id) VALUES (%s, %s)"
        
        cursor.execute(sql, (str(discord_id), license_id))
        db.commit()
        return True
    except mysql.connector.Error as err:
        print(f"Erro ao vincular contas: {err}")
        return False
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def buscar_dados_ranking_completo():
    db = conectar_db()
    if not db: return []
    try:
        cursor = db.cursor()
        sql = "SELECT discord_nick, total_sucata FROM inventario_sucata ORDER BY total_sucata DESC"
        cursor.execute(sql)
        return cursor.fetchall()
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def buscar_license_id(discord_id: str) -> Optional[str]:
    db = conectar_db()
    if not db: return None
    try:
        cursor = db.cursor()
        
        sql = "SELECT license_id FROM discord_fivem_map WHERE discord_id = %s" 
        cursor.execute(sql, (discord_id,))
        resultado = cursor.fetchone()
        if resultado: return resultado[0] 
        return None
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def buscar_discord_id_por_license(license_id: str) -> Optional[str]:
    db = conectar_db()
    if not db: return None
    try:
        cursor = db.cursor()
        
        sql = "SELECT discord_id FROM discord_fivem_map WHERE license_id = %s" 
        cursor.execute(sql, (license_id,))
        resultado = cursor.fetchone()
        if resultado: return resultado[0] 
        return None
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def atualizar_saldo_sucata(discord_id, discord_nick, quantidade_adicionada):
    db = conectar_db()
    if not db: return None, None
    try:
        cursor = db.cursor()
        cursor.execute("SELECT total_sucata FROM inventario_sucata WHERE discord_id = %s", (discord_id,))
        resultado = cursor.fetchone()
        
        if resultado:
            total_anterior = resultado[0]
            novo_total = total_anterior + quantidade_adicionada
            
            if discord_nick.startswith("Audit_"):
                sql = "UPDATE inventario_sucata SET total_sucata = %s, audit = %s WHERE discord_id = %s"
                cursor.execute(sql, (novo_total, discord_nick, discord_id))
            else:
                sql = "UPDATE inventario_sucata SET total_sucata = %s, discord_nick = %s, audit = NULL WHERE discord_id = %s"
                cursor.execute(sql, (novo_total, discord_nick, discord_id))
        else:
            total_anterior = 0
            novo_total = quantidade_adicionada
            audit_val = discord_nick if discord_nick.startswith("Audit_") else None
            nick_val = "Desconhecido" if discord_nick.startswith("Audit_") else discord_nick
            
            sql = "INSERT INTO inventario_sucata (discord_id, discord_nick, total_sucata, audit) VALUES (%s, %s, %s, %s)"
            cursor.execute(sql, (discord_id, nick_val, novo_total, audit_val))
            
        db.commit()
        return novo_total, total_anterior
    except mysql.connector.Error as err:
        print(f"Erro no MySQL: {err}")
        return None, None
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def verificar_log_processado(log_discord_id: str) -> bool:
    db = conectar_db()
    if not db: return False
    try:
        cursor = db.cursor()
        sql = "SELECT log_discord_id FROM logs_processados WHERE log_discord_id = %s"
        cursor.execute(sql, (log_discord_id,))
        return cursor.fetchone() is not None
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def registrar_log_processado(log_discord_id: str):
    db = conectar_db()
    if not db: return
    try:
        cursor = db.cursor()
        sql = "INSERT INTO logs_processados (log_discord_id, processado_em) VALUES (%s, NOW())"
        cursor.execute(sql, (log_discord_id,))
        db.commit()
    except mysql.connector.Error as err:
        if err.errno != 1062: 
            print(f"Erro ao registrar log processado: {err}")
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def extract_log_content_from_message(message) -> str:
    text_sources = []
    if message.content: text_sources.append(message.content)
    if message.embeds:
        for embed in message.embeds:
            if embed.description: text_sources.append(embed.description)
            for field in embed.fields:
                if field.value: text_sources.append(field.value)
    return "\n---\n".join(text_sources)

def parse_log_data(log_text: str) -> Optional[Dict]:
   
    pattern = re.compile(
        r"[\s\S]*?O jogador \*\*?(?P<nome>.*?)\*\*?\s?"
        r"\(license:(?P<license>[0-9a-f]+).*?\) \*\*?(?P<acao>pegou|colocou)\*\*? o item \*\*?(?P<item>.*?)\*\*? x(?P<quantidade>\d+)", re.DOTALL
    )
    match = pattern.search(log_text)
    if match:
        data = match.groupdict()
        return {
            'player_name': data['nome'].strip(),
            'license_id': data['license'].strip().lower(), 
            'action': data['acao'].lower(),
            'item': data['item'].strip(),
            'quantity': int(data['quantidade'])
        }
    return None


async def buscar_log_e_processar_imagem(discord_id: int, license_id: str, attachment: Attachment) -> Optional[Dict]:
    """
    Busca um log de depósito >= QUANTIDADE_MINIMA_DEPOSITO e só então 
    executa o OCR para validar. Economiza recursos de Cloud Vision.
    """
    channel = client.get_channel(CANAL_ID_DE_LOGS)
    if not channel: return None

    time_limit = datetime.now(timezone.utc) - timedelta(minutes=BUSCA_IMEDIATA_LOOKBACK_MINUTES)
    
    print(f"INICIANDO BUSCA DE LOGS: License {license_id}, Mínimo: {QUANTIDADE_MINIMA_DEPOSITO}")

    try:
        async for message in channel.history(limit=100, after=time_limit):
            if verificar_log_processado(str(message.id)): continue
                
            log_text = extract_log_content_from_message(message)
            log_data = parse_log_data(log_text)
            
            
            if log_data and log_data['action'] == 'colocou' and log_data['item'].lower() == ITEM_DE_LOG_ALVO:
                if log_data['license_id'] == license_id: 
                    
                   
                    if log_data['quantity'] < QUANTIDADE_MINIMA_DEPOSITO:
                        continue 
                        
                    
                    
                    print(f"Log de depósito (Qtd: {log_data['quantity']}) encontrado. Processando imagem...")
                    
                    
                    data = await attachment.read()
                    img_inventario = Image.open(BytesIO(data))
                    quantidade_extraida = extrair_quantidade_sucata(img_inventario) 
                    
                    if quantidade_extraida <= 0:
                        print("OCR leu 0 ou valor inválido. Ignorando este log e continuando a busca.")
                        continue
                        
                    
                    if abs(log_data['quantity'] - quantidade_extraida) <= FUZZY_MATCH_MARGEM:
                        
                        log_data['discord_message_id'] = str(message.id)
                        return {
                            'log': log_data,
                            
                            'quantidade_imagem': quantidade_extraida 
                        }
                    else:
                        print(f"Log ({log_data['quantity']}) e Imagem ({quantidade_extraida}) não conferem (Margem: {FUZZY_MATCH_MARGEM}). Buscando o próximo log...")
        
        return None

    except Exception as e:
        print(f"ERRO CRÍTICO na busca: {e}")
        return None





def cadastrar_produto(nome: str, valor: float, discord_id: str) -> bool:
    db = conectar_db()
    if not db: return False
    try:
        cursor = db.cursor()
        sql = "INSERT INTO produtos_cadastrados (nome, valor_base, cadastrado_por_discord_id) VALUES (%s, %s, %s)"
        cursor.execute(sql, (nome, valor, discord_id))
        db.commit()
        return True
    except mysql.connector.Error as err:
        if err.errno == 1062:
            return False
        print(f"Erro ao cadastrar produto: {err}")
        return False
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def buscar_catalogo_ativo() -> List[Tuple]:
    db = conectar_db()
    if not db: return []
    try:
        cursor = db.cursor()
        sql = "SELECT id, nome, valor_base FROM produtos_cadastrados WHERE ativo = TRUE ORDER BY nome"
        cursor.execute(sql)
        return cursor.fetchall()
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def adicionar_item_orcamento(discord_id: str, nome_produto: str, valor: float, quantidade: int = 1) -> bool:
    db = conectar_db()
    if not db: return False
    try:
        cursor = db.cursor()
        sql = "INSERT INTO orcamentos_itens (discord_id, nome_produto, valor, quantidade) VALUES (%s, %s, %s, %s)"
        cursor.execute(sql, (discord_id, nome_produto, valor, quantidade))
        db.commit()
        return True
    except mysql.connector.Error as err:
        print(f"Erro ao adicionar item de orçamento: {err}")
        return False
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def listar_orcamento(discord_id: str) -> List[Tuple]:
    db = conectar_db()
    if not db: return []
    try:
        cursor = db.cursor()
        
        sql = "SELECT id, nome_produto, valor, quantidade FROM orcamentos_itens WHERE discord_id = %s ORDER BY adicionado_em"
        cursor.execute(sql, (discord_id,))
        return cursor.fetchall()
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def calcular_total_orcamento(discord_id: str) -> float:
    db = conectar_db()
    if not db: return 0.0
    try:
        cursor = db.cursor()
        sql = "SELECT SUM(valor * quantidade) FROM orcamentos_itens WHERE discord_id = %s"
        cursor.execute(sql, (discord_id,))
        resultado = cursor.fetchone()
        return float(resultado[0]) if resultado and resultado[0] is not None else 0.0
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def limpar_orcamento(discord_id: str) -> bool:
    db = conectar_db()
    if not db: return False
    try:
        cursor = db.cursor()
        sql = "DELETE FROM orcamentos_itens WHERE discord_id = %s"
        cursor.execute(sql, (discord_id,))
        db.commit()
        return cursor.rowcount > 0
    except mysql.connector.Error as err:
        print(f"Erro ao limpar orçamento: {err}")
        return False
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()

def remover_item_orcamento(id_item: int, discord_id: str) -> bool:
    db = conectar_db()
    if not db: return False
    try:
        cursor = db.cursor()
        sql = "DELETE FROM orcamentos_itens WHERE id = %s AND discord_id = %s"
        cursor.execute(sql, (id_item, discord_id))
        db.commit()
        return cursor.rowcount > 0
    except mysql.connector.Error as err:
        print(f"Erro ao remover item de orçamento: {err}")
        return False
    finally:
        if db and db.is_connected():
            cursor.close()
            db.close()




async def criar_ou_encontrar_canal_orcamento(guild: discord.Guild, member: discord.Member):
    channel_name = f"orçamento-{member.name.lower().replace(' ', '-')}"
    
    
    for channel in guild.channels:
        if channel.name == channel_name:
            return channel
    
    
    try:
     
        category = discord.utils.get(guild.categories, id=CATEGORIA_ID_ORCAMENTO)
        
    
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False), # Ninguém vê por padrão
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True), # O usuário pode ler e escrever
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True) # O bot pode ler e escrever
        }
        
       
        new_channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Canal de orçamento privado para {member.display_name}"
        )
        await new_channel.send(f"👋 Bem-vindo ao seu canal de orçamento privado, {member.mention}! Use `!orcamento` aqui para gerenciar.")
        return new_channel
        
    except discord.Forbidden:
        print(f"ERRO: Bot não tem permissão para criar canais na categoria {CATEGORIA_ID_ORCAMENTO}.")
        return None
    except Exception as e:
        print(f"Erro ao criar canal: {e}")
        return None



def criar_embed_orcamento(nick_usuario: str, itens: List[Tuple], total: float) -> discord.Embed:
    embed = discord.Embed(
        title=f"💰 Orçamento de {nick_usuario}",
        color=discord.Color.gold()
    )
    
    if not itens:
        embed.description = "Seu orçamento está vazio! Use o menu abaixo para selecionar um produto."
    else:
        lista_itens = ""
       
        for id_item, nome, valor, quantidade in itens:
            subtotal = valor * quantidade
            lista_itens += f"[`{id_item}`] **{quantidade}x** {nome} (R$ {valor:.2f} cada) = **R$ {subtotal:.2f}**\n"
        
        embed.add_field(
            name="📋 Itens Atuais (Use `!remover_item [ID]` para excluir)", 
            value=lista_itens, 
            inline=False
        )
            
    embed.add_field(
        name="💲 TOTAL FINAL", 
        value=f"**R$ {total:.2f}**", 
        inline=False
    )
    
    embed.set_footer(text="Selecione um item no menu para adicionar ao orçamento.")
    return embed


async def atualizar_mensagem_orcamento(interaction: discord.Interaction, discord_id: str):
    try:
        
        itens = listar_orcamento(discord_id)
        total = calcular_total_orcamento(discord_id)
        embed = criar_embed_orcamento(interaction.user.display_name, itens, total)
        
       
        view = OrcamentoView() 
        
       
        if interaction.message:
            await interaction.message.edit(embed=embed, view=view)
    except Exception as e:
        print(f"Erro ao atualizar mensagem de orçamento: {e}")


class QuantidadeModal(discord.ui.Modal, title="Definir Quantidade"):
    def __init__(self, produto_id: int, nome_produto: str, valor_base: float, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.produto_id = produto_id
        self.nome_produto = nome_produto
        self.valor_base = valor_base
        
        self.quantidade_input = discord.ui.TextInput(
            label=f"Qtd. de {nome_produto} (R$ {valor_base:.2f})",
            placeholder="Digite a quantidade (Ex: 5)",
            default="1", 
            min_length=1,
            max_length=4,
            required=True
        )
        self.add_item(self.quantidade_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True) 
        try:
            quantidade = int(self.quantidade_input.value.strip())
            if quantidade <= 0:
                await interaction.followup.send("❌ Quantidade inválida.", ephemeral=True)
                return

            if adicionar_item_orcamento(str(interaction.user.id), self.nome_produto, self.valor_base, quantidade):
                await interaction.followup.send(
                    f"✅ **{quantidade}x {self.nome_produto}** adicionado ao seu orçamento.", 
                    ephemeral=True
                )
                await atualizar_mensagem_orcamento(interaction, str(interaction.user.id))
            else:
                await interaction.followup.send("❌ Erro ao adicionar o item.", ephemeral=True)
        except ValueError:
            await interaction.followup.send("❌ A quantidade deve ser um número inteiro.", ephemeral=True)

class ProdutoSelect(discord.ui.Select):
    def __init__(self):
        produtos = buscar_catalogo_ativo()
        options = []
        if not produtos:
             options.append(discord.SelectOption(label="Nenhum produto cadastrado.", value="0"))
        else:
            for id_prod, nome, valor in produtos:
                options.append(
                    discord.SelectOption(
                        label=nome,
                        value=str(id_prod),
                        description=f"R$ {valor:.2f}"
                    )
                )
        
        super().__init__(
            placeholder="Selecione um produto para orçar...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        
        try:
            if self.values[0] == "0":
                await interaction.response.send_message("❌ Não há produtos para selecionar.", ephemeral=True)
                return

            produto_id = int(self.values[0])
            produtos = buscar_catalogo_ativo()
            
            produto_selecionado = next((p for p in produtos if p[0] == produto_id), None)
            
            if produto_selecionado:
                id_prod, nome, valor = produto_selecionado
                modal = QuantidadeModal(id_prod, nome, valor)
                
                
                await interaction.response.send_modal(modal)
            else:
                await interaction.response.send_message("❌ Produto não encontrado no catálogo.", ephemeral=True)
                
        except Exception as e:
            print(f"Erro no callback do ProdutoSelect: {e}")
            
            if not interaction.response.is_done():
                 await interaction.response.send_message("❌ Erro interno ao processar a seleção. Tente novamente.", ephemeral=True)
            


class OrcamentoView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(ProdutoSelect())

    @discord.ui.button(label="🔄 Recarregar", style=discord.ButtonStyle.secondary, emoji="🔄", row=1)
    async def recarregar(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await atualizar_mensagem_orcamento(interaction, str(interaction.user.id))

    @discord.ui.button(label="🧹 Limpar Orçamento", style=discord.ButtonStyle.danger, emoji="🧹", row=1)
    async def limpar_carrinho(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        discord_id = str(interaction.user.id)
        if limpar_orcamento(discord_id):
            await interaction.followup.send("🗑️ Seu orçamento foi **limpo** com sucesso.", ephemeral=True)
            await atualizar_mensagem_orcamento(interaction, discord_id)
        else:
            await interaction.followup.send("⚠️ Seu orçamento já estava vazio.", ephemeral=True)


class RegistroModal(discord.ui.Modal, title="Vincular Conta FiveM"):
    license_input = discord.ui.TextInput(
        label="Sua License ID (FiveM)",
        placeholder="Ex: 8d1fc10a58554b9ba211f94b91decbfcb43b6f7f", 
        min_length=10,
        max_length=100,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        license_id = self.license_input.value.strip().lower()
        
      
        prefix = "license:"
        if license_id.startswith(prefix):
            license_id = license_id[len(prefix):]
       
        discord_id = str(interaction.user.id)
        if vincular_discord_license(discord_id, license_id):
            await interaction.response.send_message(f"✅ Conta vinculada à license: `{license_id}`.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Erro ao salvar no banco.", ephemeral=True)

class RegistroView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) 
    @discord.ui.button(label="🔗 Vincular License", style=discord.ButtonStyle.primary, emoji="🆔")
    async def abrir_registro(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(RegistroModal())

class RelatorioView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="📊 Baixar Relatório (Excel)", style=discord.ButtonStyle.success, emoji="📥")
    async def exportar_excel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True) 
        try:
            dados = buscar_dados_ranking_completo()
            if not dados:
                await interaction.followup.send("❌ Banco de dados vazio.")
                return
            df = pd.DataFrame(dados, columns=['Nome Discord', 'Total Farmado'])
            data_hoje = datetime.now().strftime("%d/%m/%Y %H:%M")
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Ranking')
                worksheet = writer.sheets['Ranking']
                worksheet.column_dimensions['A'].width = 25
                worksheet.column_dimensions['B'].width = 15
            buffer.seek(0)
            arquivo = discord.File(buffer, filename=f"Relatorio_{datetime.now().strftime('%Y%m%d')}.xlsx")
            await interaction.followup.send(content=f"✅ **Relatório Gerado!**\n📅 {data_hoje}", file=arquivo)
        except Exception as e:
            print(f"Erro Excel: {e}")
            await interaction.followup.send("❌ Erro interno.")




intents = discord.Intents.default()
intents.message_content = True 
intents.guild_messages = True
intents.members = True 
client = discord.Client(intents=intents)

async def auditoria_de_saldo_loop():
    await client.wait_until_ready()
    channel = client.get_channel(CANAL_ID_DE_LOGS)
    if not channel: return

    while not client.is_closed():
        await asyncio.sleep(INTERVALO_AUDITORIA_MINUTOS * 60)
        print(f"\n--- INICIANDO AUDITORIA ({datetime.now().strftime('%H:%M:%S')}) ---")
        time_limit = datetime.now(timezone.utc) - timedelta(minutes=INTERVALO_AUDITORIA_MINUTOS + 2)
        audit_pattern = re.compile(
            r"[\s\S]*?O jogador \*\*?(?P<nome>.*?) \(license:(?P<license>[0-9a-f]+).*?\) \*\*?(?P<acao>pegou)\*\*? o item \*\*?(?P<item>.*?)\*\*? x(?P<quantidade>\d+)", re.DOTALL
        )
        try:
            async for message in channel.history(limit=100, after=time_limit):
                if verificar_log_processado(str(message.id)): continue
                log_text = extract_log_content_from_message(message)
                match = audit_pattern.search(log_text)
                
                if match and match.group('item').strip().lower() == ITEM_DE_LOG_ALVO:
                    license_id = match.group('license').strip()
                    quantidade = int(match.group('quantidade'))
                    
                    
                    if quantidade < QUANTIDADE_MINIMA_AUDITORIA:
                        registrar_log_processado(str(message.id)) 
                        continue 
                    

                    discord_id = buscar_discord_id_por_license(license_id) 
                    if discord_id:
                        
                        novo_total, _ = atualizar_saldo_sucata(discord_id, f"Audit_{license_id}", -quantidade)
                        if novo_total is not None:
                            print(f"AUDITORIA: {discord_id} | -{quantidade}. Novo: {novo_total}")
                            try:
                                user = await client.fetch_user(int(discord_id))
                                await user.send(f"⚠️ **AUDITORIA:** Você pegou {quantidade}x sucatas. Saldo: **{novo_total}**.")
                            except: pass
                    registrar_log_processado(str(message.id))
        except Exception as e:
            print(f"ERRO AUDITORIA: {e}")

@client.event
async def on_ready():
    print(f'🤖 Bot conectado como {client.user}')
    client.loop.create_task(auditoria_de_saldo_loop())



@client.event
async def on_message(message):
    if message.author == client.user: return

    content = message.content.lower()

    
    if content == '!comandos_bot':
        embed = discord.Embed(
            title="🤖 Comandos do Bot de Gestão",
            description="Aqui estão todas as funcionalidades disponíveis para você:",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="📝 `!enviar_sucata [Nick]`", 
            value=(
                "Envie um print do seu inventário com este comando na legenda.\n"
                "**Uso:** Anexe a imagem e escreva `!enviar_sucata SeuNick`."
            ),
            inline=False
        )
        
        embed.add_field(
            name="🆔 `!painel_registro`", 
            value="Exibe o botão para você vincular sua **License ID** ao Discord.", 
            inline=False
        )
        
        embed.add_field(
            name="📊 `!painel_adm`", 
            value="Exibe o botão para baixar o **Relatório Excel** (Admin).", 
            inline=False
        )

        embed.add_field(
            name="💰 `!orcamento`", 
            value="**Inicia** seu canal de orçamento privado. O gerenciamento é feito lá.", 
            inline=False
        )

        embed.add_field(
            name="➕ `!cadastrar_produto [Nome] [Valor]`", 
            value="Adiciona um novo produto ao catálogo.", 
            inline=False
        )
        
        embed.add_field(
            name="❌ `!remover_item [ID]`", 
            value="Remove um item específico do seu orçamento (use o ID que aparece no painel).", 
            inline=False
        )
        
        embed.set_footer(text="Bot de Gestão de Farm - Sistema Automático")
        await message.channel.send(embed=embed)
        return
    

    if content == '!painel_registro':
        view = RegistroView()
        await message.channel.send(
            "🆔 **Vincular Conta FiveM**\nClique no botão para cadastrar sua License ID.",
            view=view
        )
        return

    if content == '!painel_adm':
        view = RelatorioView()
        await message.channel.send(
            "📊 **Painel de Gestão (Admin)**\nClique para baixar o relatório Excel.",
            view=view
        )
        return

    

    if content.startswith('!enviar_sucata'):
        try:
            partes = message.content.split(' ', 1)
            nick_personagem = partes[1].strip() if len(partes) > 1 else None

            if not nick_personagem or not message.attachments:
                 await message.channel.send("❌ Uso: `!enviar_sucata [Nick]` com a imagem anexada.")
                 return
            
            attachment = message.attachments[0]
            if not attachment.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                 await message.channel.send("❌ Imagem inválida. Aceitamos .png, .jpg, .jpeg.")
                 return

            license_id_jogador = buscar_license_id(str(message.author.id)) 
            if not license_id_jogador:
                 await message.channel.send("❌ **Conta não vinculada!** Use `!painel_registro`.")
                 return
            
            await message.channel.send(f"🔍 Buscando log de depósito de **{QUANTIDADE_MINIMA_DEPOSITO}+** sucatas. Aguarde, não envie novamente.")
            
            
            log_e_imagem = await buscar_log_e_processar_imagem(message.author.id, license_id_jogador, attachment)

            if log_e_imagem:
                log_validado = log_e_imagem['log']
                quantidade_extraida = log_e_imagem['quantidade_imagem']

                novo_total, _ = atualizar_saldo_sucata(message.author.id, nick_personagem, quantidade_extraida)
                registrar_log_processado(log_validado['discord_message_id'])
                
                await message.channel.send(
                    f"✅ **Log Validado!** Log de **{log_validado['quantity']}** encontrado e imagem confirmada (**{quantidade_extraida}**).\n"
                    f"📈 **Novo Total:** **{novo_total}**."
                )
            else:
                await message.channel.send(f"❌ Nenhum log de **COLOCAÇÃO** de **{QUANTIDADE_MINIMA_DEPOSITO} ou mais** sucatas encontrado nos últimos {BUSCA_IMEDIATA_LOOKBACK_MINUTES} min, ou a quantidade lida na imagem não confere.")

        except Exception as e:
            print(f"Erro no !enviar_sucata: {e}")
            await message.channel.send("❌ Erro interno ao processar a requisição.")




    if content.startswith('!cadastrar_produto'):
       
        try:
            partes = message.content.split(' ', 2)
            if len(partes) < 3:
                await message.channel.send("❌ Uso: `!cadastrar_produto [Nome] [Valor]`")
                return

            nome_produto = partes[1].strip()
            valor = float(partes[2].replace(',', '.'))
            
            if valor <= 0:
                await message.channel.send("❌ O valor deve ser positivo.")
                return

            if cadastrar_produto(nome_produto, valor, str(message.author.id)):
                await message.channel.send(f"✅ Produto **{nome_produto}** (R$ {valor:.2f}) cadastrado no catálogo.")
            else:
                await message.channel.send(f"❌ Erro ao cadastrar. O produto '{nome_produto}' pode já existir.")

        except ValueError:
            await message.channel.send("❌ Erro: O Valor informado é inválido.")
        except Exception as e:
            print(f"Erro ao cadastrar produto: {e}")
            await message.channel.send("❌ Erro interno.")
        return


    if content == '!orcamento':
        if not message.guild:
            await message.channel.send("❌ Este comando deve ser usado em um servidor.")
            return

        orcamento_channel = await criar_ou_encontrar_canal_orcamento(message.guild, message.author)

        if not orcamento_channel:
            await message.channel.send("❌ Não foi possível criar/encontrar seu canal de orçamento privado.")
            return

       
        if message.channel.id != orcamento_channel.id:
            await message.channel.send(f"✅ Seu orçamento está no canal privado: {orcamento_channel.mention}")
        
        try:
            discord_id = str(message.author.id)
            itens = listar_orcamento(discord_id)
            total = calcular_total_orcamento(discord_id)
            
            embed = criar_embed_orcamento(message.author.display_name, itens, total)
            view = OrcamentoView()
            
            await orcamento_channel.send(embed=embed, view=view)

        except Exception as e:
            print(f"Erro ao exibir orçamento: {e}")
            await orcamento_channel.send("❌ Erro interno ao exibir orçamento.")
        return
    
    if content.startswith('!remover_item'):
        try:
            partes = message.content.split(' ', 1)
            if len(partes) < 2:
                await message.channel.send("❌ Uso: `!remover_item [ID do Item]`. Verifique o ID no `!orcamento`.")
                return
            
            id_item = int(partes[1].strip())
            discord_id = str(message.author.id)

            if remover_item_orcamento(id_item, discord_id):
                
                orcamento_channel = await criar_ou_encontrar_canal_orcamento(message.guild, message.author)
                
                if orcamento_channel and orcamento_channel.id == message.channel.id:
                     await message.channel.send(f"✅ Item com ID `{id_item}` removido. Recarregue o painel!", delete_after=5)
                else:
                    await message.channel.send(f"✅ Item com ID `{id_item}` removido do seu orçamento.")
            else:
                await message.channel.send(f"❌ Item com ID `{id_item}` não encontrado no seu orçamento.")

        except ValueError:
            await message.channel.send("❌ Erro: O ID do item deve ser um número inteiro.")
        except Exception as e:
            print(f"Erro ao remover item: {e}")
            await message.channel.send("❌ Erro interno.")
        return


client.run(DISCORD_TOKEN)