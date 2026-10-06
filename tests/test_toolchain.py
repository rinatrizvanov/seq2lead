"""Smoke tests for the numerical toolchain the later milestones depend on.

These check that RDKit and PyTorch are installed and functioning on this machine.
None of them exercises project logic, because none of that logic exists yet.
"""


def test_rdkit_parses_and_canonicalizes_smiles() -> None:
    from rdkit import Chem

    mol = Chem.MolFromSmiles("C1=CC=CC=C1")
    assert mol is not None
    assert Chem.MolToSmiles(mol) == "c1ccccc1"


def test_rdkit_inchi_backend_is_present_and_working() -> None:
    """Pins one published InChIKey so a broken or missing InChI backend fails here.

    Scope: this is a toolchain check on RDKit's InChI bridge for a single neutral,
    salt-free molecule. It says nothing about compound standardization — parent
    selection, salt and solvate stripping, charge normalization and tautomer
    handling are all untested, because that pipeline does not exist yet. It gets
    its own tests, against curated reference cases, when it is written at M3.
    """
    from rdkit import Chem

    key = Chem.MolToInchiKey(Chem.MolFromSmiles("CC(=O)Oc1ccccc1C(=O)O"))  # aspirin
    assert key == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"


def test_torch_imports_and_reports_a_backend() -> None:
    import torch

    assert torch.tensor([1.0, 2.0]).sum().item() == 3.0
    assert isinstance(torch.backends.mps.is_available(), bool)
