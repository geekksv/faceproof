const { expect } = require("chai");
const { ethers } = require("hardhat");

const H = (s) => ethers.keccak256(ethers.toUtf8Bytes(s));
const POST = "https://www.youtube.com/shorts/t7K38auPbPI";

describe("FaceProofRegistry", function () {
  let registry, owner, other;

  beforeEach(async function () {
    [owner, other] = await ethers.getSigners();
    const Factory = await ethers.getContractFactory("FaceProofRegistry");
    registry = await Factory.deploy();
    await registry.waitForDeployment();
  });

  it("starts empty", async function () {
    expect(await registry.total()).to.equal(0n);
    expect(await registry.isAnchored(H("anything"))).to.equal(false);
  });

  it("anchors a proof and reads it back intact", async function () {
    const evidence = H("evidence-bundle-v1");
    const face = H("face-image-bytes");

    await expect(registry.anchor(evidence, face, 9947, POST, "YouTube"))
      .to.emit(registry, "ProofAnchored")
      .withArgs(evidence, face, owner.address, 9947, POST, "YouTube", anyUint());

    expect(await registry.isAnchored(evidence)).to.equal(true);
    expect(await registry.total()).to.equal(1n);

    const p = await registry.getProof(evidence);
    expect(p.evidenceHash).to.equal(evidence);
    expect(p.faceHash).to.equal(face);
    expect(p.submitter).to.equal(owner.address);
    expect(p.similarityBps).to.equal(9947);
    expect(p.postUrl).to.equal(POST);
    expect(p.platform).to.equal("YouTube");
    expect(p.timestamp).to.be.greaterThan(0n);
  });

  it("rejects a duplicate anchor so records can never be overwritten", async function () {
    const evidence = H("evidence-bundle-v1");
    await registry.anchor(evidence, H("f"), 9000, POST, "YouTube");

    await expect(
      registry.connect(other).anchor(evidence, H("different"), 1, "https://evil.example", "X")
    ).to.be.revertedWithCustomError(registry, "AlreadyAnchored");

    // The original record is untouched by the failed overwrite attempt.
    const p = await registry.getProof(evidence);
    expect(p.submitter).to.equal(owner.address);
    expect(p.postUrl).to.equal(POST);
  });

  it("rejects the zero hash", async function () {
    await expect(
      registry.anchor(ethers.ZeroHash, H("f"), 1, POST, "YouTube")
    ).to.be.revertedWithCustomError(registry, "EmptyHash");
  });

  it("reverts for evidence that was never anchored -- the tamper case", async function () {
    const real = H("evidence-bundle-v1");
    await registry.anchor(real, H("f"), 9947, POST, "YouTube");

    // Flip one character of the post URL: a completely different digest.
    const tampered = H("evidence-bundle-v2");
    expect(tampered).to.not.equal(real);
    expect(await registry.isAnchored(tampered)).to.equal(false);
    await expect(registry.getProof(tampered)).to.be.revertedWithCustomError(
      registry,
      "UnknownProof"
    );
  });

  it("enumerates anchors in insertion order", async function () {
    const a = H("a"), b = H("b");
    await registry.anchor(a, H("fa"), 100, "https://x.com/a", "X (Twitter)");
    await registry.anchor(b, H("fb"), 200, "https://x.com/b", "X (Twitter)");

    expect(await registry.total()).to.equal(2n);
    expect(await registry.hashAt(0)).to.equal(a);
    expect(await registry.hashAt(1)).to.equal(b);
  });

  it("records the submitter, not an arbitrary claimed address", async function () {
    const evidence = H("submitted-by-other");
    await registry.connect(other).anchor(evidence, H("f"), 5000, POST, "YouTube");
    const p = await registry.getProof(evidence);
    expect(p.submitter).to.equal(other.address);
  });
});

// Hardhat-chai-matchers exposes anyUint through its withArgs helpers; the
// block timestamp is not predictable, so we only assert it is a uint.
function anyUint() {
  const { anyUint } = require("@nomicfoundation/hardhat-chai-matchers/withArgs");
  return anyUint;
}
