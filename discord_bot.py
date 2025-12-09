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



DISCORD_TOKEN = 'SEU_DISCORD_ID' 
CANAL_ID_DE_LOGS = 0000000000000000  
ITEM_DE_LOG_ALVO = 'sucalixo'
INTERVALO_AUDITORIA_MINUTOS = 20 
FUZZY_MATCH_MARGEM = 5 
BUSCA_IMEDIATA_LOOKBACK_MINUTES = 5 


def extract_log_content_from_message(message) -> str:
    """Extrai o texto do log de todas as fontes possíveis."""
    
    text_sources = []
    
    if message.content: text_sources.append(message.content)
        
    if message.embeds:
        for embed in message.embeds:
            if embed.description: text_sources.append(embed.description)
            for field in embed.fields:
                if field.value: text_sources.append(field.value)
                
    return "\n---\n".join(text_sources)


def parse_log_data(log_text: str) -> Optional[Dict]:
    """Tenta extrair todos os dados (nome, license, ação, item, quantidade) do log."""
    
    
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



intents = discord.Intents.default()
intents.message_content = True 
intents.guild_messages = True
client = discord.Client(intents=intents)


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
            sql = "UPDATE inventario_sucata SET total_sucata = %s, discord_nick = %s WHERE discord_id = %s"
            cursor.execute(sql, (novo_total, discord_nick, discord_id))
        else:
            total_anterior = 0
            novo_total = quantidade_adicionada
            sql = "INSERT INTO inventario_sucata (discord_id, discord_nick, total_sucata) VALUES (%s, %s, %s)"
            cursor.execute(sql, (discord_id, discord_nick, novo_total))
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




async def buscar_log_proximo(discord_message, discord_id, license_id, quantidade_imagem):
    """
    Busca logs de colocação recentes do jogador que batem com a quantidade da imagem 
    (com margem de erro) e exporta os resultados para JSON.
    """
    
    channel = client.get_channel(CANAL_ID_DE_LOGS)
    if not channel: return False

   
    time_limit = datetime.now(timezone.utc) - timedelta(minutes=BUSCA_IMEDIATA_LOOKBACK_MINUTES)
    
    logs_encontrados = []
    log_validado = None

    print(f"INICIANDO BUSCA DE LOGS: License {license_id}, Qtd Imagem: {quantidade_imagem}, Lookback: {BUSCA_IMEDIATA_LOOKBACK_MINUTES} min.")

    try:
        async for message in channel.history(limit=100, after=time_limit):
            
            
            if verificar_log_processado(str(message.id)):
                continue
                
            log_text = extract_log_content_from_message(message)
            log_data = parse_log_data(log_text)
            
            if log_data and log_data['action'] == 'colocou' and log_data['item'].lower() == ITEM_DE_LOG_ALVO:
                
                
                if log_data['license_id'] == license_id:
                    
                    logs_encontrados.append({
                        'license_id': log_data['license_id'],
                        'quantity_log': log_data['quantity'],
                        'log_message': log_text,
                        'discord_message_id': str(message.id),
                        'log_timestamp': message.created_at.isoformat(),
                        'match_type': 'EXATO' if log_data['quantity'] == quantidade_imagem else 'PRÓXIMO'
                    })

                    
                    if abs(log_data['quantity'] - quantidade_imagem) <= FUZZY_MATCH_MARGEM:
                        
                        
                        if log_validado is None:
                            log_validado = log_data
                            log_validado['discord_message_id'] = str(message.id)

        
        if logs_encontrados:
            filepath = f'log_procurado_depositante_{discord_id}.json'
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(logs_encontrados, f, ensure_ascii=False, indent=4)
            await discord_message.channel.send(f"📊 **DEBUG:** Logs recentes encontrados e salvos em `{filepath}`. Total: {len(logs_encontrados)} logs.")


      
        return log_validado

    except Exception as e:
        print(f"ERRO CRÍTICO na busca imediata: {e}")
        return None



async def auditoria_de_saldo_loop():
    """Executa a auditoria de logs no histórico (foco em 'pegou') a cada 20 minutos."""
    await client.wait_until_ready()
    
    channel = client.get_channel(CANAL_ID_DE_LOGS)
    if not channel: return

    while not client.is_closed():
        
        await asyncio.sleep(INTERVALO_AUDITORIA_MINUTOS * 60)
        
        print(f"\n--- INICIANDO AUDITORIA DE SUBTRAÇÃO ({datetime.now().strftime('%H:%M:%S')}) ---")
        
        time_limit = datetime.now(timezone.utc) - timedelta(minutes=INTERVALO_AUDITORIA_MINUTOS + 2)
        
        
        audit_pattern = re.compile(
            r"[\s\S]*?O jogador \*\*?(?P<nome>.*?) "
            r"\(license:(?P<license>[0-9a-f]+).*?\) \*\*?(?P<acao>pegou)\*\*? o item \*\*?(?P<item>.*?)\*\*? x(?P<quantidade>\d+)", re.DOTALL
        )

        try:
            async for message in channel.history(limit=100, after=time_limit):
                
                if verificar_log_processado(str(message.id)):
                    continue

                log_text = extract_log_content_from_message(message)
                match = audit_pattern.search(log_text)
                
                if match and match.group('item').strip().lower() == ITEM_DE_LOG_ALVO:
                    
                    license_id = match.group('license').strip()
                    quantidade = int(match.group('quantidade'))
                    discord_id = buscar_discord_id_por_license(license_id) 
                    
                    if discord_id:
                        
                        
                        novo_total, total_anterior = atualizar_saldo_sucata(discord_id, f"Audit_{license_id}", -quantidade)
                        
                        if novo_total is not None and novo_total >= 0:
                            print(f"AUDITORIA SUBTRAÇÃO: {discord_id} | -{quantidade}. Novo total: {novo_total}")
                            
                            try:
                                user = await client.fetch_user(int(discord_id))
                                await user.send(
                                    f"⚠️ **AUDITORIA AUTOMÁTICA:** Detectamos que você **pegou** ({quantidade}x {ITEM_DE_LOG_ALVO}) do baú. "
                                    f"Este valor foi **SUBTRAÍDO** do seu saldo de sucata. Novo Total: **{novo_total}**."
                                )
                            except Exception as dm_err:
                                pass
                        elif novo_total is not None and novo_total < 0:
                            atualizar_saldo_sucata(discord_id, f"Audit_{license_id}", quantidade) 
                            print(f"AUDITORIA ALERTA: Subtração de {quantidade} negada. Saldo ficaria negativo.")

                    registrar_log_processado(str(message.id))

        except Exception as e:
            print(f"ERRO CRÍTICO na auditoria de log: {e}")




@client.event
async def on_ready():
    print(f'🤖 Bot conectado como {client.user}')
    
   
    client.loop.create_task(auditoria_de_saldo_loop())

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    user_id = message.author.id
    user_id_str = str(user_id)

    
    if message.content.lower().startswith('!enviar_sucata'):
        try:
            
            partes = message.content.split(' ', 1)
            nick_personagem = partes[1].strip() if len(partes) > 1 else None

            if not nick_personagem or not message.attachments:
                 await message.channel.send("❌ Uso incorreto. Por favor, use: `!enviar_sucata [NickDoSeuPersonagem]` e **envie a imagem junto**.")
                 return
            
            attachment = message.attachments[0]
            if not attachment.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
                 await message.channel.send("❌ Por favor, envie uma imagem válida (PNG/JPG).")
                 return

           
            await message.channel.send(f"Processando imagem de {nick_personagem}...")
            data = await attachment.read()
            img_inventario = Image.open(BytesIO(data))
            quantidade_extraida = extrair_quantidade_sucata(img_inventario)
            
            if quantidade_extraida <= 0:
                 await message.channel.send(f"⚠️ Não foi possível ler a quantidade de {TEXTO_ALVO} na imagem.")
                 return

      
            license_id_jogador = buscar_license_id(user_id_str) 
            if not license_id_jogador:
                 await message.channel.send("❌ Sua License ID do FiveM não está mapeada no sistema.")
                 return
            
    
            await message.channel.send("🔍 Buscando logs de colocação nos últimos 5 minutos...")
            
            log_validado = await buscar_log_proximo(
                message, 
                user_id, 
                license_id_jogador, 
                quantidade_extraida
            )

            if log_validado:

                novo_total, total_anterior = atualizar_saldo_sucata(
                    user_id, nick_personagem, quantidade_extraida
                )
                
               
                registrar_log_processado(log_validado['discord_message_id'])

                await message.channel.send(
                    f"✅ **SUCESSO NA VALIDAÇÃO!** Log encontrado (Qtd: {log_validado['quantity']}) e saldo adicionado."
                    f"\n📈 **NOVO TOTAL CUMULATIVO:** **{novo_total}**."
                )
            else:
               
                await message.channel.send(
                    f"❌ **FALHA NA VALIDAÇÃO!** Nenhum log de **COLOCAÇÃO** próximo a **{quantidade_extraida}** "
                    f"unidades foi encontrado nos últimos {BUSCA_IMEDIATA_LOOKBACK_MINUTES} minutos. "
                    f"Certifique-se de depositar {ITEM_DE_LOG_ALVO} e usar o comando rapidamente."
                )

        except Exception as e:
            print(f"Erro no processamento do depósito: {e}")
            await message.channel.send("❌ Ocorreu um erro interno ao processar sua requisição.")


client.run(DISCORD_TOKEN)