from typing import List, Dict
from src.blockchain.transaction import Transaction
from src.utils.logger import logger
from src.utils.database import db_connection
from src.p2p.network import P2PNetwork
import json
import time
import sqlite3

class Mempool:
    def __init__(self):
        self.transactions: Dict[str, Transaction] = {}
        self.max_size = 1000
        self.p2p_network = P2PNetwork
        # بارگذاری فقط در صورتی که جدول mempool وجود دارد
        try:
            self._load_from_db()
        except sqlite3.OperationalError:
            logger.warning("Mempool table not found, starting with empty mempool")

    def _load_from_db(self):
        """بارگذاری تراکنش‌ها فقط اگر جدول وجود دارد"""
        with db_connection() as conn:
            cursor = conn.cursor()
            # بررسی وجود جدول mempool
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mempool'")
            if not cursor.fetchone():
                return
                
            cursor.execute('SELECT * FROM mempool')

    def add_transaction(self, tx: Transaction) -> bool:
        """اضافه کردن تراکنش جدید به mempool"""
        try:
            # اعتبارسنجی اولیه تراکنش
            if not tx.tx_hash or tx.tx_hash != tx.calculate_hash():
                logger.error("Invalid transaction hash")
                return False
            
            if tx.tx_hash in self.transactions:
                logger.warning(f"Transaction {tx.tx_hash[:8]} already in mempool")
                return False
            
            if len(self.transactions) >= self.max_size:
                logger.warning("Mempool is full, transaction rejected")
                return False
            
            if tx.tx_hash not in self.transactions:
                if hasattr(self, 'p2p_network'):
                    self.p2p_network.broadcast_transaction(tx)
            
            # ذخیره در حافظه
            self.transactions[tx.tx_hash] = tx
            
            # ذخیره در دیتابیس
            with db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                INSERT OR IGNORE INTO mempool (
                    tx_hash, sender, recipient, 
                    amount, data, timestamp, signature
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    tx.tx_hash,
                    tx.sender,
                    tx.recipient,
                    tx.amount,
                    json.dumps(tx.data),
                    tx.timestamp,
                    tx.signature
                ))
                conn.commit()
            
            logger.info(f"Transaction added to mempool: {tx.tx_hash[:8]}")
            return True
        except Exception as e:
            logger.error(f"Failed to add transaction: {e}")
            return False

    def get_transactions(self, max_count: int = 10) -> List[Transaction]:
        """دریافت تراکنش‌ها برای ساخت بلاک جدید"""
        # اولویت‌بندی بر اساس کارمزد یا timestamp
        sorted_txs = sorted(
            self.transactions.values(),
            key=lambda tx: tx.timestamp
        )
        return sorted_txs[:max_count]

    def remove_transactions(self, tx_hashes: List[str]):
        """حذف تراکنش‌های تایید شده از mempool"""
        with db_connection() as conn:
            cursor = conn.cursor()
            for tx_hash in tx_hashes:
                # حذف از حافظه
                if tx_hash in self.transactions:
                    del self.transactions[tx_hash]
                
                # حذف از دیتابیس
                cursor.execute('DELETE FROM mempool WHERE tx_hash = ?', (tx_hash,))
            conn.commit()
        
        logger.info(f"Removed {len(tx_hashes)} transactions from mempool")

    def clear_expired(self, expiry_seconds: int = 3600):
        """پاک‌سازی تراکنش‌های منقضی شده"""
        now = time.time()
        expired = [
            tx_hash for tx_hash, tx in self.transactions.items()
            if now - tx.timestamp > expiry_seconds
        ]
        
        with db_connection() as conn:
            cursor = conn.cursor()
            for tx_hash in expired:
                # حذف از حافظه
                del self.transactions[tx_hash]
                
                # حذف از دیتابیس
                cursor.execute('DELETE FROM mempool WHERE tx_hash = ?', (tx_hash,))
            conn.commit()
        
        logger.info(f"Cleared {len(expired)} expired transactions")