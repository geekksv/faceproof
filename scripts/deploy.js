// Deploys FaceProofRegistry and records the address where the Python
// pipeline looks for it (deployments/<network>.json).
const fs = require("fs");
const path = require("path");
const hre = require("hardhat");

async function main() {
  const net = hre.network.name;
  const [deployer] = await hre.ethers.getSigners();
  const balance = await hre.ethers.provider.getBalance(deployer.address);

  console.log(`network   : ${net}`);
  console.log(`deployer  : ${deployer.address}`);
  console.log(`balance   : ${hre.ethers.formatEther(balance)} ETH`);

  if (balance === 0n) {
    throw new Error(
      `Deployer has no funds on ${net}. Fund it from a faucet ` +
        `(testnet coins are free) or deploy to --network localhost.`
    );
  }

  const Registry = await hre.ethers.getContractFactory("FaceProofRegistry");
  const registry = await Registry.deploy();
  await registry.waitForDeployment();

  const address = await registry.getAddress();
  const tx = registry.deploymentTransaction();
  const receipt = await tx.wait();

  const record = {
    network: net,
    chainId: Number((await hre.ethers.provider.getNetwork()).chainId),
    address,
    deployer: deployer.address,
    deployTxHash: tx.hash,
    blockNumber: receipt.blockNumber,
    deployedAt: new Date().toISOString(),
  };

  const dir = path.join(__dirname, "..", "deployments");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `${net}.json`), JSON.stringify(record, null, 2));

  console.log(`\nFaceProofRegistry deployed`);
  console.log(`address   : ${address}`);
  console.log(`tx        : ${tx.hash}`);
  console.log(`block     : ${receipt.blockNumber}`);
  console.log(`saved     : deployments/${net}.json`);

  const explorers = {
    sepolia: "https://sepolia.etherscan.io",
    amoy: "https://amoy.polygonscan.com",
    baseSepolia: "https://sepolia.basescan.org",
  };
  if (explorers[net]) console.log(`explorer  : ${explorers[net]}/address/${address}`);
}

main().catch((e) => {
  console.error(e.message || e);
  process.exitCode = 1;
});
