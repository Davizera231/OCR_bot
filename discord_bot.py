import discord
import mysql.connector
from io import BytesIO
from PIL import Image

from ocr_processor import extrair_quantidade_sucata, TEXTO_ALVO 
import re
import time
from typing import Optional, Dict
import asyncio 
from datetime import datetime, timedelta, timezone 
import json 
import pandas as pd 


DISCORD_TOKEN = 'SEU_TOKEN_AQUI' 
CANAL_ID_DE_LOGS = 0  
ITEM_DE_LOG_ALVO = 'sucalixo'
INTERVALO_AUDITORIA_MINUTOS = 20 
FUZZY_MATCH_MARGEM = 5 
BUSCA_IMEDIATA_LOOKBACK_MINUTES = 5 


DB_CONFIG = {
    'user': 'root',
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
            'license_id': data['license'].strip(),
            'action': data['acao'].lower(),
            'item': data['item'].strip(),
            'quantity': int(data['quantidade'])
        }
    return None

async def buscar_log_proximo(discord_message, discord_id, license_id, quantidade_imagem):
    channel = client.get_channel(CANAL_ID_DE_LOGS)
    if not channel: return False

    time_limit = datetime.now(timezone.utc) - timedelta(minutes=BUSCA_IMEDIATA_LOOKBACK_MINUTES)
    
    logs_encontrados = []
    log_validado = None

    print(f"INICIANDO BUSCA DE LOGS: License {license_id}, Qtd: {quantidade_imagem}")

    try:
        async for message in channel.history(limit=100, after=time_limit):
            if verificar_log_processado(str(message.id)): continue
                
            log_text = extract_log_content_from_message(message)
            log_data = parse_log_data(log_text)
            
            if log_data and log_data['action'] == 'colocou' and log_data['item'].lower() == ITEM_DE_LOG_ALVO:
                if log_data['license_id'] == license_id:
                    if abs(log_data['quantity'] - quantidade_imagem) <= FUZZY_MATCH_MARGEM:
                        if log_validado is None:
                            log_validado = log_data
                            log_validado['discord_message_id'] = str(message.id)
        return log_validado

    except Exception as e:
        print(f"ERRO CRÍTICO na busca imediata: {e}")
        return None




class RegistroModal(discord.ui.Modal, title="Vincular Conta FiveM"):
    license_input = discord.ui.TextInput(
        label="Sua License ID (FiveM)",
        placeholder="Ex: license:239129301293812",
        min_length=10,
        max_length=100,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        license_id = self.license_input.value.strip()
        if not license_id.startswith("license:"):
            await interaction.response.send_message("❌ O ID deve começar com `license:`.", ephemeral=True)
            return
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
            title="🤖 Comandos do Bot de Sucata",
            description="Aqui estão todas as funcionalidades disponíveis para você:",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="📝 `!enviar_sucata [Nick]`", 
            value=(
                "Envie um print do seu inventário com este comando na legenda.\n"
                "**Uso:** Anexe a imagem e escreva `!enviar_sucata SeuNick`.\n"
                "O bot vai ler a quantidade e somar ao seu saldo."
            ),
            inline=False
        )
        
        embed.add_field(
            name="🆔 `!painel_registro`", 
            value="Exibe o botão para você vincular sua **License ID** ao Discord. (Obrigatório para usar o bot).", 
            inline=False
        )
        
        embed.add_field(
            name="📊 `!painel_adm`", 
            value="Exibe o botão para baixar o **Relatório Excel** com o ranking de todos os membros. (Apenas Admins).", 
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
                 await message.channel.send("❌ Uso: `!enviar_sucata [Nick]` com a imagem.")
                 return
            
            attachment = message.attachments[0]
            if not attachment.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                 await message.channel.send("❌ Imagem inválida.")
                 return

            await message.channel.send(f"Processando imagem de {nick_personagem}...")
            data = await attachment.read()
            img_inventario = Image.open(BytesIO(data))
            
            quantidade_extraida = extrair_quantidade_sucata(img_inventario)
            
            if quantidade_extraida <= 0:
                 await message.channel.send(f"⚠️ Não consegui ler a quantidade de {TEXTO_ALVO}.")
                 return

            license_id_jogador = buscar_license_id(str(message.author.id)) 
            if not license_id_jogador:
                 await message.channel.send("❌ **Conta não vinculada!** Use `!painel_registro`.")
                 return
            
            await message.channel.send(f"🔍 OCR leu **{quantidade_extraida}**. Validando nos logs...")
            
            log_validado = await buscar_log_proximo(message, message.author.id, license_id_jogador, quantidade_extraida)

            if log_validado:
                novo_total, _ = atualizar_saldo_sucata(message.author.id, nick_personagem, quantidade_extraida)
                registrar_log_processado(log_validado['discord_message_id'])
                await message.channel.send(f"✅ **Validado!** Log encontrado ({log_validado['quantity']}).\n📈 **Novo Total:** **{novo_total}**.")
            else:
                await message.channel.send(f"❌ Nenhum log de **COLOCAÇÃO** de ~{quantidade_extraida} encontrado nos últimos 5 min.")

        except Exception as e:
            print(f"Erro: {e}")
            await message.channel.send("❌ Erro interno.")

client.run(DISCORD_TOKEN)