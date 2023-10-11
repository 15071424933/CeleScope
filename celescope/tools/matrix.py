import gzip
import os

import scipy.io
import scipy.sparse
import pandas as pd
import numpy as np

from celescope.tools.__init__ import (BARCODE_FILE_NAME, FEATURE_FILE_NAME, MATRIX_FILE_NAME)
from celescope.tools import utils
import sys

ROW = 'geneID'
COLUMN = 'Barcode'

  
class Features:
    def __init__(self, gene_id: list, gene_name=None, gene_type=None):
        """
        Args:
            gene_id: list of gene id
            gene_name: list of gene name
            type: ype of features, e.g. [gene, protein]
        """
        self.gene_id = list(gene_id)
        if not gene_name:
            self.gene_name = self.gene_id
        else:
            self.gene_name = list(gene_name)
        self.gene_type = gene_type 

    @classmethod
    def from_tsv(cls, tsv_file):
        if not os.path.exists(tsv_file):
            sys.exit(f'ERROR: {tsv_file} does not exist. \nSOLUTION: Rename and gzip the features file to {FEATURE_FILE_NAME}')
        df = pd.read_csv(tsv_file, sep='\t', on_bad_lines='skip', names=['gene_id', 'gene_name', 'type'], dtype=str)
        gene_id = df['gene_id'].tolist()
        gene_name = df['gene_name'].tolist()
        # avoid adding extra \t to genes.tsv when all the gene_type are Nan
        # if gene_type is None and add to dataframe, will cause Seurat::Read error: Error in FUN(X[[i]], ...) : # # subscript out of bounds
        if df['type'].isnull().sum() == len(df['type']):
            gene_type = None
        else:
            gene_type = df['type'].tolist()
        return cls(gene_id, gene_name, gene_type)

    def to_tsv(self, tsv_file):
        """
        if gene_type is None and add to dataframe, will cause Seurat::Read10X error: Error in FUN(X[[i]], ...) : subscript out of bounds
        """
        if self.gene_type:
            df = pd.DataFrame({'gene_id': self.gene_id, 'gene_name': self.gene_name, 'gene_type': self.gene_type})
        else:
            df = pd.DataFrame({'gene_id': self.gene_id, 'gene_name': self.gene_name})
        df.to_csv(tsv_file, sep='\t', index=False, header=False)


class CountMatrix:
    def __init__(self, features: Features, barcodes: list, matrix):
        """
        Args:
            features: Features object
            barcodes: list of barcodes
            matrix: scipy.sparse.coo_matrix
        """
        self.__features = features
        self.__barcodes = barcodes
        self.__matrix = matrix
        self.shape = matrix.shape
    
    @staticmethod
    @utils.add_log
    def read_barcodes(matrix_dir):
        """
            returns: barcodes set
        """
        barcode_file = utils.get_matrix_file_path(matrix_dir, BARCODE_FILE_NAME)
        barcodes, _ = utils.read_one_col(barcode_file)
        return set(barcodes)

    @classmethod
    @utils.add_log
    def from_matrix_dir(cls, matrix_dir):
        features_tsv = utils.get_matrix_file_path(matrix_dir, FEATURE_FILE_NAME)
        features = Features.from_tsv(tsv_file=features_tsv)
        barcode_file = utils.get_matrix_file_path(matrix_dir, BARCODE_FILE_NAME)
        barcodes, _ = utils.read_one_col(barcode_file)
        matrix_path = utils.get_matrix_file_path(matrix_dir, MATRIX_FILE_NAME)
        matrix= scipy.io.mmread(matrix_path)

        return cls(features, barcodes, matrix)

    def to_matrix_dir(self, matrix_dir):
        utils.check_mkdir(dir_name=matrix_dir)
        self.__features.to_tsv(f'{matrix_dir}/{FEATURE_FILE_NAME}')
        pd.Series(self.__barcodes).to_csv(f'{matrix_dir}/{BARCODE_FILE_NAME}', index=False, sep='\t', header=False)
        matrix_path = f'{matrix_dir}/{MATRIX_FILE_NAME}'
        with gzip.open(matrix_path, 'wb') as f:
            scipy.io.mmwrite(f, self.__matrix)

    @classmethod
    def from_dataframe(cls, df, features: Features, barcodes=None, value="UMI"):
        """
        Use all gene_id from features even if it is not in df
        Args:
            df: count details dataframe with columns UMI and multi-index (column, row).
            features: Features
            barcodes: only keep barcode in barcodes
            value: value name in df, UMI
            
        """
        if not barcodes:
            barcodes = df.index.levels[0].tolist()
        
        feature_index_dict = {}
        for index, gene_id in enumerate(features.gene_id):
            feature_index_dict[gene_id] = index

        barcode_index_dict = {}
        for index, barcode in enumerate(barcodes):
            barcode_index_dict[barcode] = index

        # use all barcodes
        barcode_codes = [barcode_index_dict[barcode] for barcode in df.index.get_level_values(level=0)]
        # use all gene_id from features even if it is not in df
        gene_id_codes = [feature_index_dict[gene_id] for gene_id in df.index.get_level_values(level=1)]
        mtx = scipy.sparse.coo_matrix((df[value], (gene_id_codes, barcode_codes)), 
            shape=(len(features.gene_id), len(barcodes)))

        return cls(features, barcodes, mtx)

    def __str__(self):
        n_row, n_col = self.shape[0], self.shape[1]
        return f"CountMatrix object\n {n_row} x {n_col} coo_matrix"


    def __repr__(self):
        return self.__str__()

    def concat_by_barcodes(self, other):
        if other.get_barcodes() != self.get_barcodes():
            raise ValueError('barcodes are not the same')

        if inter := set(self.get_features().gene_id).intersection(set(other.get_features().gene_id)):
            raise ValueError(f'deuplicated gene_id: {inter}')

        gene_id = self.get_features().gene_id + other.get_features().gene_id
        gene_name = self.get_features().gene_name + other.get_features().gene_name
        gene_type = None
        if self.get_features().gene_type and other.get_features().gene_type:
            gene_type = self.get_features().gene_type + other.get_features().gene_type
        features = Features(gene_id, gene_name, gene_type)

        matrix = scipy.sparse.vstack([self.get_matrix(), other.get_matrix()])

        return CountMatrix(features, self.__barcodes, matrix)

    def slice_matrix(self, slice_barcodes_indices):
        """
        Args:
            slice_barcodes_indices: list of barcode indices
        Returns:
            CountMatrix object
        """
        mtx_csc = self.__matrix.tocsc()
        sliced_mtx = mtx_csc[:, slice_barcodes_indices]
        barcodes = [self.__barcodes[i] for i in slice_barcodes_indices]
        return CountMatrix(self.__features, barcodes, sliced_mtx)

    def get_barcodes(self):
        return self.__barcodes

    def get_features(self):
        return self.__features
    
    def get_matrix(self):
        return self.__matrix

    @classmethod
    def dataframe_to_matrix(cls, df, features: Features, barcodes=None, barcode_column='Barcode', feature_column='geneID'):
        """Convert a counts dataframe to a sparse counts matrix.
        :param df_counts: counts dataframe
        :type df_counts: pandas.DataFrame
        :param barcodes: list of barcodes that will map to the rows
        :type barcodes: list
        :param features: list of features that will map to the columns
        :type features: list
        :param barcode_column: column in counts dataframe to use as barcodes, defaults to `Barcode`
        :type barcode_column: str
        :param feature_column: column in counts dataframe to use as features, defaults to `geneID`
        :type feature_column: str

        :return: sparse counts matrix
        :rtype: scipy.sparse.csrmatrix
        """
        barcode_indices = {barcode: i for i, barcode in enumerate(barcodes)}
        feature_indices = {feature: i for i, feature in enumerate(features.gene_id)}

        matrix = scipy.sparse.lil_matrix((len(barcodes), len(features.gene_id)), dtype=np.float32)
        for (barcode, feature), count in df.groupby([barcode_column, feature_column], sort=False,
                                                        observed=True).size().items():
            matrix[barcode_indices[barcode], feature_indices[feature]] = count

        return matrix.tocsr()
