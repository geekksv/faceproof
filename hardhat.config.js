require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

const PK = process.env.PRIVATE_KEY;
const accounts = PK ? [PK.startsWith("0x") ? PK : `0x${PK}`] : [];

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.24",
    settings: { optimizer: { enabled: true, runs: 200 } },
  },
  networks: {
    // Always-available local chain: `npx hardhat node` in a second terminal.
    localhost: { url: "http://127.0.0.1:8545" },

    // Public testnets. Coins are free from a faucet -- no real money.
    sepolia: {
      url: process.env.SEPOLIA_RPC_URL || "https://ethereum-sepolia-rpc.publicnode.com",
      accounts,
      chainId: 11155111,
    },
    amoy: {
      url: process.env.AMOY_RPC_URL || "https://rpc-amoy.polygon.technology",
      accounts,
      chainId: 80002,
    },
    baseSepolia: {
      url: process.env.BASE_SEPOLIA_RPC_URL || "https://sepolia.base.org",
      accounts,
      chainId: 84532,
    },
  },
  paths: { sources: "./contracts", tests: "./test", artifacts: "./artifacts" },

  // Publishing the source on Etherscan is what makes the anchor independently
  // checkable: anyone can open the contract's "Read Contract" tab, paste an
  // evidence hash into getProof, and see the record without running this code
  // or trusting this repo. Get a free key at etherscan.io/myapikey.
  etherscan: {
    apiKey: process.env.ETHERSCAN_API_KEY || "",
  },
  sourcify: { enabled: false },
};
