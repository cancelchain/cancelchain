class CCError(Exception):
    def __init__(self, message=None):
        msg = message or self.__class__.__name__
        super().__init__(msg)
        if isinstance(msg, (str, bytes)):
            self.messages = [msg]
        else:
            self.messages = msg


class InvalidWalletError(CCError):
    pass


class InvalidKeyError(InvalidWalletError):
    pass


class NoPrivateKeyError(InvalidWalletError):
    pass


class InvalidTransactionError(CCError):
    pass


class InvalidTransactionIdError(InvalidTransactionError):
    pass


class InvalidSignatureError(InvalidTransactionError):
    pass


class FutureTransactionError(InvalidTransactionError):
    pass


class ExpiredTransactionError(InvalidTransactionError):
    pass


class OutOfOrderTransactionError(InvalidTransactionError):
    pass


class UnsealedTransactionError(InvalidTransactionError):
    pass


class MissingWalletError(InvalidTransactionError):
    pass


class InsufficientFundsError(InvalidTransactionError):
    pass


class ImbalancedTransactionError(InvalidTransactionError):
    pass


class MissingInflowOutflowError(InvalidTransactionError):
    pass


class InvalidInflowOutflowError(InvalidTransactionError):
    pass


class InflowOutflowAddressMismatchError(InvalidTransactionError):
    pass


class SpentTransactionError(InvalidTransactionError):
    pass


class InvalidCoinbaseError(InvalidTransactionError):
    pass


class InvalidCoinbaseErrorRewardError(InvalidCoinbaseError):
    pass


class InvalidBlockError(CCError):
    pass


class InvalidBlockHashError(InvalidBlockError):
    pass


class InvalidPreviousHashError(InvalidBlockError):
    pass


class InvalidMerkleRootError(InvalidBlockError):
    pass


class MissingCoinbaseError(InvalidBlockError):
    pass


class SealedBlockError(InvalidBlockError):
    pass


class UnlinkedBlockError(InvalidBlockError):
    pass


class FutureBlockError(InvalidBlockError):
    pass


class InvalidProofError(InvalidBlockError):
    pass


class OutOfOrderBlockError(InvalidBlockError):
    pass


class InvalidBlockIndexError(InvalidBlockError):
    pass


class InvalidTargetError(InvalidBlockError):
    pass


class MissingBlockError(InvalidBlockError):
    pass


class InvalidChainError(CCError):
    pass


class EmptyChainError(InvalidChainError):
    pass


class MissingPreviousBlockError(InvalidChainError):
    pass
