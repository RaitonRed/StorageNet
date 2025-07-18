from typing import List, Optional
from src.blockchain.block import Block
from src.blockchain.transaction import Transaction
from src.blockchain.consensus import Consensus
from src.blockchain.repositories import BlockRepository, TransactionRepository
from src.utils.logger import logger
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from src.blockchain.validator_registry import ValidatorRegistry
from src.utils.database import init_db, db_connection
import os
import time

class Blockchain:
    def __init__(self, difficulty: int = 4):
        self.difficulty = difficulty
        self.chain = []
    
        try:
            from src.utils.database import init_db
            init_db()
        
            self.chain = self.load_chain()
            if not self.chain:
                self._initialize_new_chain()
            else:
                # اگر زنجیره نامعتبر بود، دیتابیس را ریست کنیم
                if not Consensus.is_chain_valid(self.chain):
                    logger.warning("Invalid chain detected, resetting database...")
                    self._reset_blockchain()
                    self._initialize_new_chain()
                
        except Exception as e:
            logger.error(f"Chain initialization failed: {e}")
            self._reset_blockchain()
            self._initialize_new_chain()

    def _reset_blockchain(self):
        """پاکسازی دیتابیس و شروع مجدد"""
        logger.warning("Resetting blockchain database...")
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.executescript('''
            DROP TABLE IF EXISTS blocks;
            DROP TABLE IF EXISTS transactions;
            DROP TABLE IF EXISTS mempool;
            ''')
            conn.commit()
        init_db()

    def _initialize_new_chain(self):
        """مقداردهی اولیه یک زنجیره جدید"""
        logger.info("Initializing new blockchain")
        try:
            # ایجاد بلاک جنسیس
            genesis_block = self._create_genesis_block()
            self.chain = [genesis_block]
            logger.info("New blockchain initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize new chain: {e}")
            raise RuntimeError("Failed to initialize blockchain") from e

    def _create_genesis_block(self) -> Block:
        """ایجاد بلاک جنسیس"""
        genesis_tx = Transaction(
            sender="0",
            recipient="0",
            amount=0,
            data={"type": "genesis", "message": "Initial block of the chain"}
        )
        
        genesis_block = Block(
            index=0,
            timestamp=0,
            transactions=[genesis_tx],
            previous_hash="0",
            difficulty=self.difficulty
        )
        
        # اثبات کار برای بلاک جنسیس
        genesis_block = Consensus.proof_of_work(genesis_block)
        
        # ذخیره در دیتابیس
        try:
            block_id = BlockRepository.save_block(genesis_block)
            TransactionRepository.save_transaction(genesis_tx, block_id)
            logger.info(f"Genesis block created with hash: {genesis_block.hash}")
            return genesis_block
        except Exception as e:
            logger.error(f"Failed to save genesis block: {e}")
            raise

    def load_chain(self) -> List[Block]:
        """بارگذاری زنجیره از دیتابیس"""
        chain = []
        block_count = BlockRepository.get_block_count()
        
        for index in range(block_count):
            block = BlockRepository.get_block_by_index(index)
            if not block:
                logger.error(f"Invalid block at index {index}")
                return []
                
            chain.append(block)
        
        # اعتبارسنجی زنجیره بارگذاری شده
        if not chain or not Consensus.is_chain_valid(chain):
            logger.error("Loaded chain is invalid")
            return []
            
        logger.info(f"Successfully loaded chain with {len(chain)} blocks")
        return chain

    def add_block(self, transactions: List[Transaction], validator_private_key: ec.EllipticCurvePrivateKey) -> Optional[Block]:
        """اضافه کردن بلاک جدید به زنجیره"""
        if not transactions:
            logger.warning("Cannot add empty block")
            return None
        
        last_block = self.get_last_block()
        if not last_block:
            logger.error("Chain not initialized")
            return None
        
        # ایجاد بلاک جدید
        new_block = Block(
            index=last_block.index + 1,
            timestamp=int(time.time()),  # استفاده از timestamp صحیح
            transactions=transactions,
            previous_hash=last_block.hash,
            difficulty=self.difficulty
        )
    
        # انجام اثبات کار
        new_block = Consensus.proof_of_work(new_block)

        # امضای بلاک
        public_key = validator_private_key.public_key()
        new_block.validator = public_key.public_bytes(
            Encoding.PEM,
            PublicFormat.SubjectPublicKeyInfo
        ).decode()
        new_block.sign_block(validator_private_key)
    
        # ذخیره در دیتابیس
        try:
            block_id = BlockRepository.save_block(new_block)
            TransactionRepository.save_transactions_bulk(transactions, block_id)
        
            # اضافه کردن به زنجیره در حافظه
            self.chain.append(new_block)
            logger.info(f"Block #{new_block.index} added to chain")
            return new_block
        except Exception as e:
            logger.error(f"Failed to save block: {e}")
            return None

    def get_last_block(self) -> Optional[Block]:
        """دریافت آخرین بلاک زنجیره"""
        if not self.chain:
            return None
        return self.chain[-1]

    def is_chain_valid(self) -> bool:
        """اعتبارسنجی زنجیره فعلی"""
        return Consensus.is_chain_valid(self.chain)

    def resolve_conflicts(self, nodes: List[str]) -> bool:
        """حل تعارضات با نودهای دیگر (طولانی‌ترین زنجیره معتبر)"""
        logger.info("Resolving conflicts with network nodes...")
        
        new_chain = None
        max_cumulative_diff = Consensus.cumulative_difficulty(self.chain)
        
        # در اینجا معمولاً با نودهای دیگر ارتباط برقرار می‌کنیم
        # برای سادگی، فرض می‌کنیم زنجیره‌های دیگر را دریافت کرده‌ایم
        
        # اگر زنجیره جدیدی با سختی تجمعی بیشتر پیدا شد
        if new_chain and Consensus.is_chain_valid(new_chain):
            if Consensus.cumulative_difficulty(new_chain) > max_cumulative_diff:
                self.chain = new_chain
                logger.info("Chain replaced with longer valid chain")
                return True
                
        logger.info("Current chain remains authoritative")
        return False

    def get_blocks_paginated(self, page: int = 1, per_page: int = 10) -> List[Block]:
        """دریافت بلاک‌ها به صورت صفحه‌بندی شده"""
        return BlockRepository.get_blocks_paginated(page, per_page)

    def __repr__(self) -> str:
        return f"<Blockchain length={len(self.chain)}, last_block={self.get_last_block()}>"
