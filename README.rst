CancelChain is an open-source python project that implements a custom blockchain ledger. The ledger protocol allows for the assigning of tokens to subjects (utf-8 strings of less than 80 characters) as indications of either opposition or support. Opposition entries are allowed to be rescinded later. Support is forever.

* `Project Home Page`_
* `Documentation`_


Installation
------------

Install using pip:

.. code-block:: console

    $ pip install cancelchain


Usage
-----

.. code-block::

    $ cancelchain --help
    Usage: cancelchain [OPTIONS] COMMAND [ARGS]...

    Options:
      -e, --env-file FILE   Load environment variables from this file. python-
                            dotenv must be installed.
      --version  Show the version and exit.
      --help     Show this message and exit.

    Commands:
      export    Export the block chain to file.
      import    Import the block chain from file.
      init      Initialize the database.
      mill      Start a milling process.
      routes    Show the routes for the app.
      run       Run a development server.
      shell     Run a shell in the app context.
      subject   Command group to work with subjects.
      sync      Synchronize the node's block chain to that of its peers.
      txn       Command group to create transactions.
      validate  Validate the node's block chain.
      wallet    Command group to work with wallets.


.. _Project Home Page: https://cancelchain.org
.. _Documentation: https://docs.cancelchain.org
