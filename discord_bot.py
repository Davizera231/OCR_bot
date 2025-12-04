import discord
import mysql.connector
from io import BytesIO
from PIL import Image
from ocr_processor import extrair_quantidade_sucata, TEXTO_ALVO 


DISCORD_TOKEN = 'SUA_DISCORD_TOKEN_AQUI' 


user_waiting_for_image = {} 

intents = discord.Intents.default()
intents.message_content = True 
client = discord.Client(intents=intents)


DB_CONFIG = {
    'user': 'SEU_USUARIO_MYSQL',
    'password': 'SUA_SENHA_MYSQL',
    'host': 'SEU_HOST_MYSQL', 
    'database': 'SEU_BANCO_DE_DADOS_MYSQL',
}



def conectar_db():
    """Tenta conectar ao banco de dados."""
    try:
        return mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error as err:
        print(f"Erro ao conectar ao MySQL: {err}")
        return None

def atualizar_saldo_sucata(discord_id, discord_nick, quantidade_adicionada):
    """Atualiza o saldo no banco de dados, inserindo ou atualizando o usuário."""
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
        if db.is_connected():
            cursor.close()
            db.close()



@client.event
async def on_ready():
    print(f'🤖 Bot conectado como {client.user}')

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    user_id = message.author.id

    
    if message.content.lower().startswith('!enviar_sucata'):
        
        try:
            
            nick_personagem = message.content.split(' ', 1)[1].strip()
            if not nick_personagem: raise IndexError
            
            
            user_waiting_for_image[user_id] = nick_personagem
            
            await message.channel.send(
                f"Entendido, **{nick_personagem}**! Agora, por favor, **envie a imagem** do seu inventário "
                f"com a sucata em uma mensagem separada (não use o mesmo comando de novo)."
            )
        except IndexError:
            await message.channel.send("❌ Uso incorreto. Por favor, use: `!enviar_sucata [NickDoSeuPersonagem]`")
        return

    
    
   
    if user_id in user_waiting_for_image and message.attachments:
        attachment = message.attachments[0]
        
        
        if attachment.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            
            nick_personagem = user_waiting_for_image.pop(user_id) 
            
            await message.channel.send(f"Processando imagem de {nick_personagem}...")

            
            data = await attachment.read()
            img_inventario = Image.open(BytesIO(data))
            
            
            quantidade_extraida = extrair_quantidade_sucata(img_inventario)
            
            if quantidade_extraida > 0:
                
                novo_total, total_anterior = atualizar_saldo_sucata(user_id, nick_personagem, quantidade_extraida)
                
                if novo_total is not None:
                    await message.channel.send(
                        f"🎉 **{nick_personagem}**, encontrado **{quantidade_extraida}** unidades de {TEXTO_ALVO}!"
                        f"\n📊 Seu total anterior no banco de dados era: **{total_anterior}**."
                        f"\n📈 **NOVO TOTAL CUMULATIVO:** **{novo_total}**."
                    )
                else:
                    await message.channel.send("❌ Erro grave ao salvar dados no banco de dados.")
            else:
                await message.channel.send(
                    f"⚠️ **{nick_personagem}**, não foi possível ler a quantidade de {TEXTO_ALVO}."
                    " Por favor, verifique se a imagem está clara e se o item está visível."
                )
        else:
            await message.channel.send("Por favor, envie uma imagem válida (PNG/JPG).")


client.run(DISCORD_TOKEN)