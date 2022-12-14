from dotenv import load_dotenv
from loguru import logger
import os
import discord
from logger import intercept_logging
from aiokafka import AIOKafkaConsumer
import time
import json
import logging
import asyncio

logger.info("Starting Prediction Consumer")

# Load .env file
logger.info("Loading .env file")
if load_dotenv():
    logger.success("Loaded .env file")
else:
    logger.warning("Failed to load .env file")

# Get environment variables
logger.info("Loading environment variables")
LOG_PATH = os.getenv("LOG_PATH")
logger.info(f"Log path: {LOG_PATH}")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if DISCORD_TOKEN is None or DISCORD_TOKEN == "":
    logger.critical("No Discord token found")
    exit(1)
else:
    logger.info(f"Discord token: ***")

KAFKA_BROKER = os.getenv("KAFKA_BROKER", "localhost:9092")
logger.info(f"Kafka broker: {KAFKA_BROKER}")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "modelprediction")
logger.info(f"Kafka topic: {KAFKA_TOPIC}")
DEBUG_MODE = os.getenv("DEBUG_MODE", "False") == "True"
logger.info(f"Debug mode: {DEBUG_MODE}")
WAIT_FOR_KAFKA = int(os.getenv("WAIT_FOR_KAFKA", 10))
logger.info(f"Wait for Kafka (secondes): {WAIT_FOR_KAFKA}")

# Set up logging
logger.add(LOG_PATH, rotation="1 day", retention="1 month", level="DEBUG")
intercept_logging("discord", logger, level=logging.ERROR)
intercept_logging("kafka", logger)

# Set up Discord intents
intents = discord.Intents.default()
intents.message_content = True
client = discord.AutoShardedClient(
    intents=intents,
    shard_count=3,
)

# Set up Kafka consumer
async def get_kafka_consumer():
    logger.info("Waiting for Kafka broker to be ready")
    time.sleep(WAIT_FOR_KAFKA)

    consumer = AIOKafkaConsumer(
        bootstrap_servers=KAFKA_BROKER,
        group_id="prediction-consumer",
        auto_offset_reset="earliest",
    )
    logger.info("Connected to Kafka broker")
    consumer.subscribe(topics=[KAFKA_TOPIC])
    logger.info("Subscribed to Kafka topic")
    await consumer.start()
    return consumer

async def get_discord_message(channel_id, message_id):
    logger.debug(f"Getting Discord Channels: {channel_id=}")
    discord_channel = client.get_channel(channel_id)
    if discord_channel is None:
        logger.error(f"Discord channel {channel_id} not found")
        return None
    
    try:
        logger.debug(f"Fetching Discord Message {message_id=}")
        return await discord_channel.fetch_message(message_id)
    except discord.errors.NotFound:
        logger.error(f"Discord message {message_id} not found")
        return None

# Create a decorate to catch rate limit and exit 1
def crash_after_http_exception(func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except discord.errors.HTTPException as e:
            logger.critical(e)
            exit(1)
    return wrapper

@crash_after_http_exception
async def process_discord_message(channel_id, message_id, is_toxic):
    logger.debug(f"Process discord message {message_id=} {channel_id=}")
    discord_message = await get_discord_message(channel_id, message_id)
    await asyncio.sleep(0.4) # Avoid rate limit
    
    if discord_message is None:
        return

    if not DEBUG_MODE and is_toxic:
        logger.info(f"Message {message_id=} is toxic")
        logger.debug(f"Deleting Discord Message {message_id=}")
        await discord_message.delete()
        return
    
    if DEBUG_MODE:
        if is_toxic:
            logger.info(f"Message {message_id=} is toxic")
            logger.debug(f"Add reaction to Discord Message {message_id=}")
            await discord_message.add_reaction("\N{NO ENTRY}")
        else:
            logger.info(f"Message {message_id=} is not toxic")
            logger.debug(f"Add reaction to Discord Message {message_id=}")
            await discord_message.add_reaction("\N{WHITE HEAVY CHECK MARK}")
            

@client.event
async def on_ready():
    logger.success("Discord client ready !")
    consumer = await get_kafka_consumer()
    logger.success("Consumer started !")
    logger.success("Prediction Consumer is ready !")
    try:
        async for prediction in consumer:
            prediction_str = prediction.value.decode("utf-8")
            prediction_obj = json.loads(prediction_str)
            
            channel_id = prediction_obj["channel_id"]
            message_id = prediction_obj["message_id"]
            is_toxic = prediction_obj["is_toxic"]
            
            logger.debug(f"Received prediction {message_id=} {channel_id=} {is_toxic=}")
            await process_discord_message(channel_id, message_id, is_toxic)
            await consumer.commit()
    finally:
        logger.info("Stopping consumer")
        await consumer.stop()

client.run(DISCORD_TOKEN)
