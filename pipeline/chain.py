"""Anchor evidence on-chain and verify it back.

Deliberately thin: the contract holds the truth, this module only marshals
data in and out of it. Works identically against a local Hardhat node and a
public testnet -- the only difference is where the RPC points and who signs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from web3 import Web3
from web3.exceptions import ContractLogicError

ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = (
    ROOT / "artifacts" / "contracts" / "FaceProofRegistry.sol" / "FaceProofRegistry.json"
)
DEPLOYMENTS = ROOT / "deployments"

# Hardhat's first well-known dev account. Public, worthless, and identical on
# every machine -- which is exactly why it is safe to hardcode as the local
# default. Never used for a real network: those require PRIVATE_KEY.
HARDHAT_DEV_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

DEFAULT_RPC = {
    "localhost": "http://127.0.0.1:8545",
    "sepolia": "https://ethereum-sepolia-rpc.publicnode.com",
    "amoy": "https://rpc-amoy.polygon.technology",
    "baseSepolia": "https://sepolia.base.org",
}

EXPLORERS = {
    "sepolia": "https://sepolia.etherscan.io",
    "amoy": "https://amoy.polygonscan.com",
    "baseSepolia": "https://sepolia.basescan.org",
}


class ChainError(RuntimeError):
    pass


@dataclass
class AnchorReceipt:
    tx_hash: str
    block_number: int
    gas_used: int
    contract_address: str
    network: str
    evidence_hash: str

    @property
    def explorer_url(self) -> str | None:
        base = EXPLORERS.get(self.network)
        return f"{base}/tx/{self.tx_hash}" if base else None

    def to_dict(self) -> dict:
        return {
            "network": self.network,
            "contract_address": self.contract_address,
            "tx_hash": self.tx_hash,
            "block_number": self.block_number,
            "gas_used": self.gas_used,
            "evidence_hash": self.evidence_hash,
            "explorer_url": self.explorer_url,
        }


@dataclass
class OnChainProof:
    evidence_hash: str
    face_hash: str
    submitter: str
    timestamp: int
    similarity_bps: int
    post_url: str
    platform: str

    def to_dict(self) -> dict:
        return {
            "evidence_hash": self.evidence_hash,
            "face_hash": self.face_hash,
            "submitter": self.submitter,
            "timestamp": self.timestamp,
            "similarity_bps": self.similarity_bps,
            "similarity": self.similarity_bps / 10_000,
            "post_url": self.post_url,
            "platform": self.platform,
        }


def _abi() -> list:
    if not ARTIFACT.exists():
        raise ChainError("Contract artifact missing. Compile first:\n  npx hardhat compile")
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))["abi"]


def load_deployment(network: str) -> dict:
    f = DEPLOYMENTS / f"{network}.json"
    if not f.exists():
        raise ChainError(
            f"No deployment recorded for '{network}'. Deploy it first:\n"
            f"  npx hardhat run scripts/deploy.js --network {network}"
        )
    return json.loads(f.read_text(encoding="utf-8"))


class ChainClient:
    """Connection to one network's FaceProofRegistry."""

    def __init__(
        self,
        network: str = "localhost",
        rpc_url: str | None = None,
        address: str | None = None,
        private_key: str | None = None,
    ):
        self.network = network
        self.rpc_url = (
            rpc_url
            or os.environ.get(f"{network.upper()}_RPC_URL")
            or DEFAULT_RPC.get(network)
        )
        if not self.rpc_url:
            raise ChainError(f"No RPC URL for network '{network}'")

        self.w3 = Web3(Web3.HTTPProvider(self.rpc_url, request_kwargs={"timeout": 60}))
        if not self.w3.is_connected():
            hint = (
                "\nStart the local chain in another terminal:\n  npx hardhat node"
                if network == "localhost"
                else ""
            )
            raise ChainError(f"Cannot reach RPC at {self.rpc_url}{hint}")

        self.address = Web3.to_checksum_address(
            address or load_deployment(network)["address"]
        )
        self.contract = self.w3.eth.contract(address=self.address, abi=_abi())

        key = private_key or os.environ.get("PRIVATE_KEY")
        if not key and network == "localhost":
            key = HARDHAT_DEV_KEY
        if key:
            self.account = self.w3.eth.account.from_key(
                key if key.startswith("0x") else "0x" + key
            )
        else:
            self.account = None

    # -- write -------------------------------------------------------------

    def anchor(
        self,
        evidence_hash: str,
        face_sha256: str,
        similarity_bps: int,
        post_url: str,
        platform: str,
    ) -> AnchorReceipt:
        """Write one evidence digest to the chain and wait for the receipt."""
        if self.account is None:
            raise ChainError("No signing key. Set PRIVATE_KEY in .env for public networks.")

        fn = self.contract.functions.anchor(
            Web3.to_bytes(hexstr=evidence_hash),
            Web3.to_bytes(hexstr="0x" + face_sha256.removeprefix("0x")),
            int(similarity_bps),
            post_url,
            platform or "",
        )

        try:
            gas = fn.estimate_gas({"from": self.account.address})
        except ContractLogicError as e:
            if "AlreadyAnchored" in str(e):
                raise ChainError(
                    "This exact evidence is already anchored -- the contract "
                    "refuses to overwrite an existing record."
                ) from e
            raise ChainError(f"Transaction would revert: {e}") from e

        tx = fn.build_transaction(
            {
                "from": self.account.address,
                "nonce": self.w3.eth.get_transaction_count(self.account.address),
                "gas": int(gas * 1.25),
                **self._fee_fields(),
            }
        )
        signed = self.account.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = self.w3.eth.send_raw_transaction(raw)
        rcpt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)

        if rcpt.status != 1:
            raise ChainError(f"Transaction reverted: {tx_hash.hex()}")

        return AnchorReceipt(
            tx_hash="0x" + rcpt.transactionHash.hex().removeprefix("0x"),
            block_number=rcpt.blockNumber,
            gas_used=rcpt.gasUsed,
            contract_address=self.address,
            network=self.network,
            evidence_hash=evidence_hash,
        )

    def _fee_fields(self) -> dict:
        """EIP-1559 where supported, legacy gasPrice otherwise."""
        try:
            base = self.w3.eth.get_block("latest").get("baseFeePerGas")
            if base:
                tip = self.w3.eth.max_priority_fee
                return {
                    "maxPriorityFeePerGas": tip,
                    "maxFeePerGas": base * 2 + tip,
                    "type": 2,
                }
        except Exception:
            pass
        return {"gasPrice": self.w3.eth.gas_price}

    # -- read --------------------------------------------------------------

    def is_anchored(self, evidence_hash: str) -> bool:
        return bool(
            self.contract.functions.isAnchored(Web3.to_bytes(hexstr=evidence_hash)).call()
        )

    def get_proof(self, evidence_hash: str) -> OnChainProof | None:
        """Read a proof back, or None if this digest was never anchored."""
        try:
            p = self.contract.functions.getProof(Web3.to_bytes(hexstr=evidence_hash)).call()
        except Exception as e:
            if "UnknownProof" in str(e) or "revert" in str(e).lower():
                return None
            raise
        return OnChainProof(
            evidence_hash="0x" + p[0].hex().removeprefix("0x"),
            face_hash="0x" + p[1].hex().removeprefix("0x"),
            submitter=p[2],
            timestamp=p[3],
            similarity_bps=p[4],
            post_url=p[5],
            platform=p[6],
        )

    def total(self) -> int:
        return self.contract.functions.total().call()

    def explorer_address_url(self) -> str | None:
        base = EXPLORERS.get(self.network)
        return f"{base}/address/{self.address}" if base else None
