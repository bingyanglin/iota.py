# coding=utf-8
from __future__ import absolute_import, division, print_function, \
    unicode_literals

from typing import Generator, Iterable, List, Optional, Tuple

from iota import Address, Bundle, Transaction, \
    TransactionHash
from iota.adapter import BaseAdapter
from iota.commands.core.find_transactions import FindTransactionsCommand
from iota.commands.core.get_trytes import GetTrytesCommand
from iota.commands.core.were_addresses_spent_from import \
    WereAddressesSpentFromCommand
from iota.commands.extended import FindTransactionObjectsCommand
from iota.commands.extended.get_bundles import GetBundlesCommand
from iota.commands.extended.get_latest_inclusion import \
    GetLatestInclusionCommand
from iota.crypto.addresses import AddressGenerator
from iota.crypto.types import Seed


async def iter_used_addresses(
        adapter,  # type: BaseAdapter
        seed,  # type: Seed
        start,  # type: int
        security_level=None,  # type: Optional[int]
):
    # type: (...) -> Generator[Tuple[Address, List[TransactionHash]], None, None]
    """
    Scans the Tangle for used addresses. A used address is an address that
    was spent from or has a transaction.

    This is basically the opposite of invoking ``getNewAddresses`` with
    ``count=None``.

    .. important::
        This is an async generator!

    """
    if security_level is None:
        security_level = AddressGenerator.DEFAULT_SECURITY_LEVEL

    ft_command = FindTransactionsCommand(adapter)
    wasf_command = WereAddressesSpentFromCommand(adapter)

    for addy in AddressGenerator(seed, security_level).create_iterator(start):
        ft_response = await ft_command(addresses=[addy])

        if ft_response['hashes']:
            yield addy, ft_response['hashes']
        else:
            wasf_response = await wasf_command(addresses=[addy])
            if wasf_response['states'][0]:
                yield addy, []
            else:
                break

        # Reset the commands so that we can call them again.
        ft_command.reset()
        wasf_command.reset()


async def get_bundles_from_transaction_hashes(
        adapter,
        transaction_hashes,
        inclusion_states,
):
    # type: (BaseAdapter, Iterable[TransactionHash], bool) -> List[Bundle]
    """
    Given a set of transaction hashes, returns the corresponding bundles,
    sorted by tail transaction timestamp.
    """
    transaction_hashes = list(transaction_hashes)
    if not transaction_hashes:
        return []

    # Sort transactions into tail and non-tail.
    tail_transaction_hashes = set()
    non_tail_bundle_hashes = set()

    gt_response = await GetTrytesCommand(adapter)(hashes=transaction_hashes)
    all_transactions = list(map(
        Transaction.from_tryte_string,
        gt_response['trytes'],
    ))  # type: List[Transaction]

    for txn in all_transactions:
        if txn.is_tail:
            tail_transaction_hashes.add(txn.hash)
        else:
            # Capture the bundle ID instead of the transaction hash so
            # that we can query the node to find the tail transaction
            # for that bundle.
            non_tail_bundle_hashes.add(txn.bundle_hash)

    if non_tail_bundle_hashes:
        for txn in (await FindTransactionObjectsCommand(adapter=adapter)(
                bundles=list(non_tail_bundle_hashes),
        ))['transactions']:
            if txn.is_tail:
                if txn.hash not in tail_transaction_hashes:
                    all_transactions.append(txn)
                    tail_transaction_hashes.add(txn.hash)

    # Filter out all non-tail transactions.
    tail_transactions = [
        txn
        for txn in all_transactions
        if txn.hash in tail_transaction_hashes
    ]

    # Attach inclusion states, if requested.
    if inclusion_states:
        gli_response = await GetLatestInclusionCommand(adapter)(
            hashes=list(tail_transaction_hashes),
        )

        for txn in tail_transactions:
            txn.is_confirmed = gli_response['states'].get(txn.hash)

    # Find the bundles for each transaction.
    txn_bundles = (await GetBundlesCommand(adapter)(
        transactions=[txn.hash for txn in tail_transactions]
    ))['bundles']  # type: List[Bundle]

    if inclusion_states:
        for bundle, txn in zip(txn_bundles, tail_transactions):
            bundle.is_confirmed = txn.is_confirmed

    return list(sorted(
        txn_bundles,
        key=lambda bundle_: bundle_.tail_transaction.timestamp,
    ))
