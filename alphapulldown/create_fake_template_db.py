#!/usr/bin/env python3

'''
This script generates a fake template database for AlphaFold2 from a PDB or mmCIF
template file. Mainly based on ColabFold functions. Can be used as a standalone script.

'''

import os
import sys
from pathlib import Path

from Bio.PDB import MMCIFParser
from Bio import SeqIO
from absl import logging, flags, app
from Bio.PDB.Polypeptide import three_to_one

import colabfold.utils
from colabfold.batch import validate_and_fix_mmcif, convert_pdb_to_mmcif
import shutil

FLAGS = flags.FLAGS

extra_fields = '''
#
_chem_comp.id ala
_chem_comp.type other
_struct_asym.id
_struct_asym.entity_id
_entity_poly_seq.mon_id
'''


def save_cif(cif_fn, code, chain_id, path):
    """
    Read, validate and fix CIF file using ColabFold
    o cif_fn - path to the CIF file
    o code - four letter PDB-like code
    o chain_id - chain ID of the multimeric template
    o path - path where to save the CIF file
    """
    p = MMCIFParser()
    try:
        validate_and_fix_mmcif(cif_fn)
        logging.info(f'Validated and fixed {cif_fn}!')
    except Exception as e:
        logging.warning(f'Exception: {e}'
                        f'Cannot validate and fix {cif_fn}!')
    struct = p.get_structure(code, cif_fn)
    if len(struct.child_list) > 1:
        raise Exception(f'{len(struct.child_list)} models found in {cif_fn}!')
    # Check that it's only 1 model and chain_id is in the structure
    for model in struct:
        chain_ids = [chain.id for chain in model]
    if chain_id not in chain_ids:
        logging.warning(f"Warning! SEQRES chains may be different from ATOM chains!"
                        f"Chain {chain_id} not found in {cif_fn}!"
                        f"Found chains: {chain_ids}!")
    else:
        logging.info(f'Found chain {chain_id} in {cif_fn}!')
    #cif_io.save(path)
    # cif is corrupted due to Biopython bug, just copy template instead
    out_path = Path(path) / f'{code}.cif'
    shutil.copyfile(cif_fn, out_path)
    return out_path


def extract_seqs_from_cif(file_path, chain_id):
    """
    Extract sequences from PDB/CIF file, if SEQRES records are not present,
    extract from atoms
    o file_path - path to CIF file
    o chain id - chain id
    Return:
        o list of tuples: (chain_id, sequence)
    """
    seqs = []
    # Get the SEQRES records from the structure
    for record in SeqIO.parse(file_path, "cif-seqres"):
        if record.id != chain_id:
            logging.info("Skipping chain %s", record.id)
            continue
        seqs.append((record.seq, record.id))
    if len(seqs) == 0:
        logging.info(f'No SEQRES records found in {file_path}! Parsing from atoms!')
        # Get the SEQRES records from the structure
        cif_io = colabfold.utils.CFMMCIFIO()
        p = MMCIFParser()
        struct = p.get_structure('template', file_path)
        # Iterate through all chains in all models of the structure
        for model in struct.child_list:
            for chain in model:
                if chain.id != chain_id:
                    logging.info("Skipping chain %s", chain.id)
                    continue
                else:
                    seq_chain = ''
                    for resi in chain:
                        try:
                            one_letter = three_to_one(resi.resname)
                            seq_chain += one_letter
                        except KeyError:
                            logging.warning(f'Cannot convert {resi.resname} '
                                            f'to one letter code!')
                            continue
                    seqs.append((seq_chain, chain.id))
    return seqs


def save_seqres(code, seqs, path):
    """
    o code - four letter PDB-like code
    o seqs - list of tuples: (chain_id, sequence)
    o path - path to the pdb_seqresi, unique for each chain
    Returns:
        o Path to the file
    """
    fn = path / 'pdb_seqres.txt'
    # Rewrite the file if it exists
    if os.path.exists(fn):
        os.remove(fn)
    with open(fn, 'w') as f:
        for count, seq in enumerate(seqs):
            chain = seq[1]
            s = seq[0]
            lines = f">{code}_{chain} mol:protein length:{len(s)}\n{s}\n"
            logging.info(f'Saving SEQRES for chain {chain} to {fn}!')
            logging.info(lines)
            f.write(lines)
    return fn

def create_db(argv):
    """Main function that creates a fake template database for AlphaFold2
    from a PDB/CIF template file."""
    out_path, struct_fn, chain_id = argv
    with open(struct_fn, "r") as f:
        for line in f:
            if line.startswith("_entry.id"):
                code = line.split()[1]
    if 'code' not in locals():
        code = Path(struct_fn).stem  # must be 4-letter code
    logging.info(f"Code: {code}")
    code = code.lower()
    if len(code) != 4:
        logging.warning(f'Code must have 4 characters but is {code}')

    # Create the database structure
    pdb_mmcif_dir = Path(out_path) / 'pdb_mmcif'
    mmcif_dir = pdb_mmcif_dir / 'mmcif_files'
    seqres_dir = Path(out_path) / 'pdb_seqres'
    # pdb70_dir = Path(out_path) / 'pdb70'
    try:
        Path(mmcif_dir).mkdir(parents=True)
        # Create empty obsolete.dat file
        open(pdb_mmcif_dir / 'obsolete.dat', 'a').close()
    except FileExistsError:
        logging.info("Output mmcif directory already exists!")
        logging.info("The existing database will be overwritten!")
        mmcif_files = os.listdir(mmcif_dir)
        if len(mmcif_files) > 0:
            logging.info("Removing existing mmcif files!")
            for f in mmcif_files:
                os.remove(mmcif_dir / Path(f))
    try:
        Path(seqres_dir).mkdir(parents=True)
    except FileExistsError:
        logging.info("Output mmcif directory already exists!")
        logging.info("The existing database will be overwritten!")
        if os.path.exists(seqres_dir / 'pdb_seqres.txt'):
            os.remove(seqres_dir / 'pdb_seqres.txt')

    # Convert PDB/MMCIF to the ColabFold-like CIF file
    if struct_fn.endswith('pdb'):
        logging.info(f"Converting {struct_fn} to CIF!")
        convert_pdb_to_mmcif(Path(struct_fn))
        cif = save_cif(struct_fn.replace('.pdb', '.cif'), code, chain_id, mmcif_dir)
    elif struct_fn.endswith('cif'):
        logging.info(f"Reading {struct_fn}!")
        cif = save_cif(struct_fn, code, chain_id, mmcif_dir)
    else:
        logging.error('Unknown format of ', struct_fn)
        sys.exit(1)

    # Save pdb_seqres.txt file to pdb_seqres
    seqs = extract_seqs_from_cif(cif, chain_id)
    sqrres_path = save_seqres(code, seqs, seqres_dir)
    logging.info(f"SEQRES saved to {sqrres_path}!")


if __name__ == '__main__':
    flags.DEFINE_string("path_to_multimeric_template", None,
                        "Path to the multimeric template PDB/CIF file")
    flags.DEFINE_string("multimeric_chain", None,
                        "Chain ID of the multimeric template")
    flags.mark_flags_as_required(["use_multimeric_template",
                                  "multimeric_chain"])
    app.run(create_db, argv=sys.argv)
