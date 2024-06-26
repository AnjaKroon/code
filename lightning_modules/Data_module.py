import os
import sys 
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from GraphRNN.GraphRNN_utils import GraphRNN_dataset
from GraphRNN.GraphRNN import Graph_RNN, Neighbor_Aggregation
from torch.utils.data import DataLoader, Subset
import pytorch_lightning as pl

class GraphRNNDataModule(pl.LightningDataModule):
    def __init__(self, epi_dates, flow_dataset, epi_dataset, config):
        super(GraphRNNDataModule, self).__init__()
        self.epi_dates = epi_dates
        self.flow_dataset = flow_dataset
        self.epi_dataset = epi_dataset
        self.config = config

    def setup(self, stage=None):
        self.epi_dates = self.epi_dates[:self.config["num_train_dates"] + self.config["num_validation_dates"]]
        self.data_set = GraphRNN_dataset(
            epi_dates=self.epi_dates,
            flow_dataset=self.flow_dataset,
            epi_dataset=self.epi_dataset,
            input_hor=self.config["input_hor"],
            pred_hor=self.config["pred_hor"],
            fake_data=False,
            normalize_edge_weights=self.config["normalize_edge_weights"]
        )

        train_indices = range(self.config["num_train_dates"] + 1 - self.config["input_hor"] - self.config["pred_hor"])
        val_indices = range(self.config["num_train_dates"], self.config["num_train_dates"] + self.config["num_validation_dates"] + 1 - self.config["input_hor"] - self.config["pred_hor"])

        self.train_data_set = Subset(self.data_set, train_indices)
        self.val_data_set = Subset(self.data_set, val_indices)

    def train_dataloader(self):
        return DataLoader(self.train_data_set, batch_size=self.config["batch_size"], pin_memory=True, shuffle=True, num_workers=3, persistent_workers=True)

    def val_dataloader(self):
        return DataLoader(self.val_data_set, batch_size=self.config["batch_size"], pin_memory=True, shuffle=False, num_workers=0)
