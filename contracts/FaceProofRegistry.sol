// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/**
 * @title FaceProofRegistry
 * @notice Tamper-evident anchor for face-identification findings.
 *
 * The chain never sees the photograph or the person. It stores only a
 * keccak256 digest of a canonicalised evidence bundle, plus small public
 * pointers (the post URL, the platform, the similarity score) that let an
 * auditor re-fetch the same material and recompute the digest themselves.
 *
 * Anchoring a hash rather than the data keeps the record cheap, keeps
 * personal data off a permanent public ledger, and still makes any later
 * edit to the evidence detectable: change one byte of the bundle and the
 * recomputed digest no longer equals the one stored here.
 */
contract FaceProofRegistry {
    struct Proof {
        bytes32 evidenceHash;  // keccak256 of the canonical evidence JSON
        bytes32 faceHash;      // sha256 of the input face image bytes
        address submitter;
        uint64  timestamp;     // block time the anchor was mined
        uint32  similarityBps; // cosine similarity in basis points (0-10000)
        string  postUrl;       // the social media post that matched
        string  platform;      // e.g. "YouTube", "Instagram"
    }

    /// @dev evidenceHash => anchored proof. First write wins; never mutated.
    mapping(bytes32 => Proof) private _proofs;

    /// @dev Insertion order, so a verifier can enumerate without an indexer.
    bytes32[] private _hashes;

    event ProofAnchored(
        bytes32 indexed evidenceHash,
        bytes32 indexed faceHash,
        address indexed submitter,
        uint32  similarityBps,
        string  postUrl,
        string  platform,
        uint64  timestamp
    );

    error AlreadyAnchored(bytes32 evidenceHash);
    error UnknownProof(bytes32 evidenceHash);
    error EmptyHash();

    /**
     * @notice Anchor one evidence bundle.
     * @dev Reverts on a repeat hash so an existing record can never be
     *      quietly overwritten -- immutability is the entire point.
     */
    function anchor(
        bytes32 evidenceHash,
        bytes32 faceHash,
        uint32 similarityBps,
        string calldata postUrl,
        string calldata platform
    ) external returns (uint256 index) {
        if (evidenceHash == bytes32(0)) revert EmptyHash();
        if (_proofs[evidenceHash].timestamp != 0) revert AlreadyAnchored(evidenceHash);

        _proofs[evidenceHash] = Proof({
            evidenceHash: evidenceHash,
            faceHash: faceHash,
            submitter: msg.sender,
            timestamp: uint64(block.timestamp),
            similarityBps: similarityBps,
            postUrl: postUrl,
            platform: platform
        });
        _hashes.push(evidenceHash);

        emit ProofAnchored(
            evidenceHash,
            faceHash,
            msg.sender,
            similarityBps,
            postUrl,
            platform,
            uint64(block.timestamp)
        );
        return _hashes.length - 1;
    }

    /// @notice True if this exact evidence bundle was ever anchored.
    function isAnchored(bytes32 evidenceHash) external view returns (bool) {
        return _proofs[evidenceHash].timestamp != 0;
    }

    /// @notice Read back a stored proof. Reverts if the hash is unknown --
    ///         which is exactly what tampered evidence produces.
    function getProof(bytes32 evidenceHash) external view returns (Proof memory) {
        Proof memory p = _proofs[evidenceHash];
        if (p.timestamp == 0) revert UnknownProof(evidenceHash);
        return p;
    }

    function total() external view returns (uint256) {
        return _hashes.length;
    }

    function hashAt(uint256 index) external view returns (bytes32) {
        return _hashes[index];
    }
}
